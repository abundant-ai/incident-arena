#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
BASE="${LOADGEN_GRADER_URL:-http://loadgen:9100}"
BROKER_SOCKET="${GRADER_BROKER_SOCKET:-/run/verifier-broker/grader.sock}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
rm -rf /logs/verifier/rundir
mkdir -p /logs/verifier/rundir
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt   /logs/verifier/verifier_abort.json

# A verifier failure must remain loud (non-zero exit and the original stderr),
# but it must never turn into Harbor's less useful RewardFileNotFoundError.
# Install the trap only after removing any reward left by an earlier run. The
# renderer supplies the exact zero-valued reward schema for this task family.
# That zero is not a graded score: verifier_abort.json beside it records the
# stage that aborted and the collector's terminal response, so consumers can
# tell an aborted verification from a clean run that earned zero.
stage=startup
write_verifier_abort() {
  python3 - "$1" "$stage" <<'PY' 2>/dev/null ||
import json, pathlib, sys
detail = None
p = pathlib.Path("/tmp/episode-done.json")
if sys.argv[2].startswith("collector") and p.is_file():
    detail = p.read_text(encoding="utf-8", errors="replace")[-4000:]
pathlib.Path("/logs/verifier/verifier_abort.json").write_text(
    json.dumps(
        {
            "schema_version": 1,
            "aborted": True,
            "exit_code": int(sys.argv[1]),
            "stage": sys.argv[2],
            "detail": detail,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
  printf '{"aborted": true, "exit_code": %s, "schema_version": 1, "stage": "%s"}\n'     "$1" "$stage" >/logs/verifier/verifier_abort.json
}
write_terminal_zero_reward() {
  rc=$?
  trap - EXIT
  if [ "$rc" -ne 0 ]; then
    write_verifier_abort "$rc" ||
      echo "test.sh: terminal verifier failure; FAILED to write abort record" >&2
    echo "test.sh: verifier ABORTED at stage=$stage (exit $rc); reward below is not a graded score" >&2
    reward_tmp="$(mktemp /tests/.terminal-reward.XXXXXX)" || {
      echo "test.sh: terminal verifier failure; FAILED to create zero reward temp file" >&2
      exit "$rc"
    }
    if printf '%s\n' '{"outcome":0.0,"reward":0.0,"safe_repair":0.0}' >"$reward_tmp" &&
        mv "$reward_tmp" /logs/verifier/reward.json; then
      echo "test.sh: terminal verifier failure; wrote deterministic zero reward" >&2
    else
      echo "test.sh: terminal verifier failure; FAILED to write zero reward" >&2
      rm -f "$reward_tmp"
    fi
  fi
  exit "$rc"
}
trap write_terminal_zero_reward EXIT
stage=grader_capability
test -S "$BROKER_SOCKET" || {
  echo "test.sh: verifier-only grader broker is unavailable: $BROKER_SOCKET" >&2
  exit 1
}

# Retry only the documented 503 not-ready response. Every other response fails.
stage=collector_wait
poll_deadline=$(( $(date +%s) + 4590 ))
while :; do
  status="$(curl --unix-socket "$BROKER_SOCKET" -sS -m 10 \
    -o /tmp/episode-done.json -w '%{http_code}' \
    "$BASE/grader/episode_done")" || {
      echo "test.sh: collector request failed: $BASE/grader/episode_done" >&2; exit 1;
    }
  case "$status" in
    200) break ;;
    503)
      [ "$(date +%s)" -lt "$poll_deadline" ] || {
        echo "test.sh: timed out after 4590s waiting for finalized evidence" >&2
        exit 1
      }
      sleep 3 ;;
    *)
      stage=collector_terminal
      echo "test.sh: collector returned terminal HTTP $status: $(cat /tmp/episode-done.json)" >&2
      exit 1 ;;
  esac
done
stage=collector_validate
PYTHONPATH="$SCRIPT_DIR" python3 - /tmp/episode-done.json <<'PY'
import json, pathlib, sys
from verifier.episode import validate_episode_done

p = json.loads(pathlib.Path(sys.argv[1]).read_text())
try:
    validate_episode_done(p)
except RuntimeError as exc:
    raise SystemExit(f"test.sh: collector failed: {exc}") from exc
PY

stage=bundle
curl --unix-socket "$BROKER_SOCKET" -fsS \
  "$BASE/grader/bundle" -o /tmp/grader-bundle.tar \
  || { echo "test.sh: finalized evidence bundle fetch failed" >&2; exit 1; }
tar -xf /tmp/grader-bundle.tar -C /logs/verifier/rundir
test -s /logs/verifier/rundir/ground-truth.yaml || {
  echo "test.sh: evidence bundle lacks runtime ground truth" >&2; exit 1;
}
if grep -Eq '^[[:space:]]*challenge:' /logs/verifier/rundir/ground-truth.yaml; then
  stage=challenge
  PYTHONPATH="$SCRIPT_DIR" python3 -m verifier.challenge \
    --run /logs/verifier/rundir \
    --manifest /logs/verifier/rundir/ground-truth.yaml \
    --bundle /tmp/grader-bundle.tar \
    --token-file "$BROKER_SOCKET" || {
      echo "test.sh: verifier-owned active challenge failed" >&2; exit 1;
    }
fi
stage=evaluate
if PYTHONPATH="$SCRIPT_DIR" python3 -m verifier.evaluate \
    --run /logs/verifier/rundir \
    --manifest /logs/verifier/rundir/ground-truth.yaml; then
  verifier_rc=0
else
  verifier_rc=$?
fi
test -s /logs/verifier/rundir/verdict.json || {
  echo "test.sh: verifier exited $verifier_rc without a verdict" >&2; exit 1;
}
stage=reward
PYTHONPATH="$SCRIPT_DIR" python3 - /logs/verifier/rundir/verdict.json <<'PY'
import json, pathlib, sys
from verifier.reward import rewards_from_verdict
verdict = json.loads(pathlib.Path(sys.argv[1]).read_text())
pathlib.Path("/logs/verifier/reward.json").write_text(
    json.dumps(rewards_from_verdict(verdict), indent=2, sort_keys=True) + "\n"
)
PY
echo "test.sh: evaluated finalized evidence with task-shipped verifier" >&2
