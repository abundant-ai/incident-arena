# Contributing

Each directory under `tasks/` is an exact snapshot of a current Oddish task version. Please make benchmark changes in the source task first, publish a new version to Oddish, then run `scripts/sync_tasks.py` to update this repository. That keeps the version and source hash in `tasks/manifest.json` meaningful.

Before opening a pull request, run:

```sh
python3 scripts/verify_tasks.py
bash -n scripts/run-benchmark.sh
./scripts/run-benchmark.sh 000 -a nop --dry-run
```

If you change task behavior, include an oracle run and a verifier result from Harbor in the pull request. Run a model trial as well when the change affects the agent-visible environment or instruction.
