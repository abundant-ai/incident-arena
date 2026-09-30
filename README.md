<h1 align="center">Incident Arena</h1>

<p align="center"><em>Can an agent diagnose and repair a live production-style incident?</em></p>

Incident Arena is a 20-task SRE benchmark built as [Harbor](https://github.com/abundant-ai/harbor) tasks. Each task starts a faulty Frappe, Saleor, or Slack-like service stack on an ephemeral Kubernetes cluster. The agent investigates the symptoms, repairs the system, and calls `declare_repair_complete` when it is ready for grading. The verifier checks the running service, including whether the repair survives traffic, time, or a restart where the scenario requires it.

This repository contains the **current Oddish task versions downloaded on September 30, 2026**. The exact task version, source hash, and SHA-256 digest of every file are recorded in [`tasks/manifest.json`](tasks/manifest.json). The task set is the same 20-task set used by [experiment 348dc527](https://www.oddish.app/orgs/sre-world-1783107594401664098/experiments/348dc527).

## Getting started

Use a Linux machine with Docker and enough room for an ephemeral Kubernetes cluster. The task configurations request 8 CPUs, 16 GB of memory, and 40–77 GB of storage per trial. Install the Harbor version used for these Kubernetes tasks:

```sh
uv tool install 'harbor @ git+https://github.com/abundant-ai/harbor.git@main'
```

Set the API key for the model you plan to run, then try a task:

```sh
export ANTHROPIC_API_KEY=...
./scripts/run-benchmark.sh 005 -a claude-code -m claude-sonnet-5-5 --ak reasoning_effort=high
```

For an OpenAI model:

```sh
export OPENAI_API_KEY=...
./scripts/run-benchmark.sh 006 -a codex -m gpt-6.1-sol --ak reasoning_effort=medium
```

The script runs Harbor with the local Docker environment and `launcher=k3s`. Pass any other `harbor run` options after the task number. Use `./scripts/run-benchmark.sh --list` to list tasks, or `all` to run every task sequentially. A config-only check needs no model key:

```sh
./scripts/run-benchmark.sh 000 -a nop --dry-run
```

Harbor writes trial results under `jobs/`. These tasks are long-running incident environments; a full trial can take more than an hour.

## Tasks

| # | Incident | Current version |
|---|---|---|
| 000 | [Frappe: deletes and background jobs fail](tasks/000--frappe--07-deletes-and-jobs-fail-194d9279/instruction.md) | v16 |
| 001 | [Frappe: desk and queue OOM](tasks/001--frappe--07-desk-and-queue-oom-0669b8e8/instruction.md) | v16 |
| 002 | [Frappe: desk and queue outage](tasks/002--frappe--07-desk-and-queue-outage-64526986/instruction.md) | v16 |
| 003 | [Frappe: new records and jobs fail](tasks/003--frappe--07-new-records-and-jobs-fail-f3320b4c/instruction.md) | v23 |
| 004 | [Frappe: new records and queue OOM](tasks/004--frappe--07-new-records-and-queue-oom-8ccfd9e7/instruction.md) | v16 |
| 005 | [Frappe: writes and queue stall](tasks/07-writes-and-queue-oom-f1db8f42/instruction.md) | v16 |
| 006 | [Saleor: intermittent checkout timeouts](tasks/006--saleor-spine--10-T1-statement-timeout-canary-c7dcd6d4/instruction.md) | v17 |
| 007 | [Slack: split sequencer](tasks/007--slack-spine--06-F3-split-sequencer-eef5b438/instruction.md) | v13 |
| 008 | [Slack: maintenance collision](tasks/008--slack-spine--06-F4-maintenance-collision-cc222870/instruction.md) | v14 |
| 009 | [Slack: logins, unread counts, and sends slow](tasks/009--slack-spine--06-logins-unread-and-sends-all-slow-since-noon-16e3fd23/instruction.md) | v15 |
| 010 | [Slack: logins, unread counts, and sends slower](tasks/010--slack-spine--06-logins-unread-sends-slower-cccddfb2/instruction.md) | v15 |
| 011 | [Slack: sends crawl, then storage slows](tasks/011--slack-spine--06-sends-crawl-then-store-slows-3e65c480/instruction.md) | v15 |
| 012 | [Slack: sends fail after strict mode, plausible pool](tasks/012--slack-spine--06-sends-fail-after-strict-mode-plausible-pool-d18e8739/instruction.md) | v15 |
| 013 | [Slack: sends fail after strict mode](tasks/013--slack-spine--06-sends-fail-after-strict-mode-turns-on-728f0739/instruction.md) | v15 |
| 014 | [Slack: sends fail during a compliance window](tasks/014--slack-spine--06-sends-fail-during-compliance-window-1f4b1235/instruction.md) | v14 |
| 015 | [Slack: sends fail with a strict pool](tasks/015--slack-spine--06-sends-fail-strict-pool-16-66fa1a7c/instruction.md) | v14 |
| 016 | [Slack: slow sends and minute stalls](tasks/016--slack-spine--06-sends-slow-and-stall-every-minute-83867383/instruction.md) | v15 |
| 017 | [Slack: minute stalls, then every send crawls](tasks/017--slack-spine--06-stall-every-minute-and-later-every-send-crawls-bd50bae9/instruction.md) | v14 |
| 018 | [Slack: sequencer lock leak](tasks/018--slack-spine--09-I1-seq-lock-leak-0b7c2973/instruction.md) | v14 |
| 019 | [Slack: distractor volume shell](tasks/019--slack-spine--13-P1-distractor-volume-shell-f73987d7/instruction.md) | v14 |

Task 005 has an older Oddish task ID that does not begin with `005`; the run script maps `005` to that directory. Each task folder contains its Harbor `task.toml`, `instruction.md`, environment, verifier, and reference solution. Harbor exposes only the appropriate task surfaces to the agent during a trial; readers who want a blind attempt should start with the instruction and avoid the verifier and solution folders.

## Reproducibility

Verify all downloaded files and Harbor task metadata:

```sh
python3 scripts/verify_tasks.py
```

To refresh the repository from Oddish after task versions change, use an Oddish API key with access to the SRE-World organization:

```sh
uv sync
ODDISH_API_KEY=... uv run python scripts/sync_tasks.py --experiment 348dc527
python3 scripts/verify_tasks.py
```

The sync script downloads the *current* version of each task in the experiment and replaces the checked-in task folders only after it verifies the remote source did not change mid-transfer. Update the task table above if version numbers change. The evaluation experiment used Harbor `e561c92a9c0edd907d57142789be71a30254de43`; the examples install current `main` for trying the tasks locally.

## License

Apache 2.0. Vendored upstream components retain their own notices in the task directories. Credentials and certificates embedded in the environments are benchmark fixtures for isolated runs.
