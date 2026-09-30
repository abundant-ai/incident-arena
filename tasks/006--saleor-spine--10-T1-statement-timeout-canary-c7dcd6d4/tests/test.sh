#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
BASE="${LOADGEN_GRADER_URL:-http://loadgen:9100}"
TOKEN_FILE="${GRADER_ACCESS_TOKEN_FILE:-/run/verifier/grader-access/token}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
rm -rf /logs/verifier/rundir
mkdir -p /logs/verifier/rundir
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt

# A verifier failure must remain loud (non-zero exit and the original stderr),
# but it must never turn into Harbor's less useful RewardFileNotFoundError.
# Install the trap only after removing any reward left by an earlier run. The
# renderer supplies the exact zero-valued reward schema for this task family.
write_terminal_zero_reward() {
  rc=$?
  trap - EXIT
  if [ "$rc" -ne 0 ]; then
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
test -r "$TOKEN_FILE" || {
  echo "test.sh: verifier-only grader capability is unavailable: $TOKEN_FILE" >&2
  exit 1
}
TOKEN="$(cat "$TOKEN_FILE")"
test -n "$TOKEN" || { echo "test.sh: grader capability is empty" >&2; exit 1; }
AUTH_HEADER="X-SRE-World-Grader-Access: $TOKEN"

# Retry only the documented 503 not-ready response. Every other response fails.
poll_deadline=$(( $(date +%s) + 4563 ))
while :; do
  status="$(curl -sS -m 10 -o /tmp/episode-done.json -w '%{http_code}' \
    -H "$AUTH_HEADER" "$BASE/grader/episode_done")" || {
      echo "test.sh: collector request failed: $BASE/grader/episode_done" >&2; exit 1;
    }
  case "$status" in
    200) break ;;
    503)
      [ "$(date +%s)" -lt "$poll_deadline" ] || {
        echo "test.sh: timed out after 4563s waiting for finalized evidence" >&2
        exit 1
      }
      sleep 3 ;;
    *)
      echo "test.sh: collector returned terminal HTTP $status: $(cat /tmp/episode-done.json)" >&2
      exit 1 ;;
  esac
done
PYTHONPATH="$SCRIPT_DIR" python3 - /tmp/episode-done.json <<'PY'
import json, pathlib, sys
from verifier.episode import validate_episode_done

p = json.loads(pathlib.Path(sys.argv[1]).read_text())
try:
    validate_episode_done(p)
except RuntimeError as exc:
    raise SystemExit(f"test.sh: collector failed: {exc}") from exc
PY

curl -fsS -H "$AUTH_HEADER" "$BASE/grader/bundle" -o /tmp/grader-bundle.tar \
  || { echo "test.sh: finalized evidence bundle fetch failed" >&2; exit 1; }
tar -xf /tmp/grader-bundle.tar -C /logs/verifier/rundir
test -s /logs/verifier/rundir/ground-truth.yaml || {
  echo "test.sh: evidence bundle lacks runtime ground truth" >&2; exit 1;
}
if grep -Eq '^[[:space:]]*challenge:' /logs/verifier/rundir/ground-truth.yaml; then
  PYTHONPATH="$SCRIPT_DIR" python3 -m verifier.challenge \
    --run /logs/verifier/rundir \
    --manifest /logs/verifier/rundir/ground-truth.yaml \
    --bundle /tmp/grader-bundle.tar \
    --token-file "$TOKEN_FILE" || {
      echo "test.sh: verifier-owned active challenge failed" >&2; exit 1;
    }
fi
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
PYTHONPATH="$SCRIPT_DIR" python3 - /logs/verifier/rundir/verdict.json <<'PY'
import json, pathlib, sys
from verifier.reward import rewards_from_verdict
verdict = json.loads(pathlib.Path(sys.argv[1]).read_text())
pathlib.Path("/logs/verifier/reward.json").write_text(
    json.dumps(rewards_from_verdict(verdict), indent=2, sort_keys=True) + "\n"
)
PY
echo "test.sh: evaluated finalized evidence with task-shipped verifier" >&2
