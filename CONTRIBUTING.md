# Contributing

Task folders are imported from Oddish. Make task changes at the source, then refresh this repository:

```sh
uv sync
ODDISH_API_KEY=... uv run python scripts/sync_tasks.py
```

The importer checks the files during transfer and records their hashes in `tasks/manifest.json`.

Before opening a pull request, run:

```sh
python3 scripts/verify_tasks.py
bash -n scripts/run-benchmark.sh
./scripts/run-benchmark.sh 000 -a nop --dry-run
```

If you change task behavior, include an oracle run and a verifier result from Harbor in the pull request. Run a model trial as well when the change affects the agent-visible environment or instruction.
