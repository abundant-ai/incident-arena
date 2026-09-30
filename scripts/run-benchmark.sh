#!/usr/bin/env bash
# Run one (or all) Incident Arena tasks with Harbor's local k3s launcher.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/run-benchmark.sh <000-019|all|--list> [harbor run options]

Examples:
  scripts/run-benchmark.sh 005 -a claude-code -m claude-sonnet-5-5
  scripts/run-benchmark.sh 006 -a codex -m gpt-6.1-sol --ak reasoning_effort=medium
  scripts/run-benchmark.sh 000 -a nop --dry-run

The runner uses Harbor's Docker environment with launcher=k3s. Remaining
arguments are passed to harbor run. Use --list to see every task.
EOF
}

resolve_task() {
  local slot="$1"
  local matches=()
  if [[ "$slot" == "005" ]]; then
    matches=("$repo_root"/tasks/07-writes-and-queue-oom-*)
  elif [[ "$slot" =~ ^0[01][0-9]$ ]]; then
    matches=("$repo_root"/tasks/"$slot"--*)
  else
    return 1
  fi
  [[ ${#matches[@]} -eq 1 && -f "${matches[0]}/task.toml" ]] || return 1
  printf '%s\n' "${matches[0]}"
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 2
fi

selector="$1"
shift

if [[ "$selector" == "--list" ]]; then
  for slot_number in {0..19}; do
    printf -v slot '%03d' "$slot_number"
    task_dir="$(resolve_task "$slot")" || { echo "Missing task $slot" >&2; exit 1; }
    printf '%s  %s\n' "$slot" "${task_dir##*/}"
  done
  exit 0
fi

if [[ "$selector" == "all" ]]; then
  for slot_number in {0..19}; do
    printf -v slot '%03d' "$slot_number"
    task_dir="$(resolve_task "$slot")" || { echo "Missing task $slot" >&2; exit 1; }
    echo "Running $slot: ${task_dir##*/}" >&2
    harbor run -p "$task_dir" -e docker --ek launcher=k3s "$@"
  done
  exit 0
fi

task_dir="$(resolve_task "$selector")" || { usage >&2; echo "Unknown task: $selector" >&2; exit 2; }
exec harbor run -p "$task_dir" -e docker --ek launcher=k3s "$@"
