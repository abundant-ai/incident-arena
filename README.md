<h1 align="center">Incident Arena</h1>

<p align="center"><em>Can an agent diagnose and repair a live production-style incident?</em></p>

Incident Arena has 20 SRE incidents across Frappe, Saleor, and a Slack-like service. Each [Harbor](https://github.com/abundant-ai/harbor) task starts a broken service stack. An agent investigates and repairs the incident, then the benchmark checks whether the service is healthy.

## Getting started

Use a Linux machine with Docker. Each task needs 8 CPUs, 16 GB of memory, and 40–77 GB of storage. Clone the repo and install Harbor:

```sh
git clone https://github.com/abundant-ai/incident-arena.git
cd incident-arena
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

Use `./scripts/run-benchmark.sh --list` to list tasks, or `all` in place of a task number to run all 20 sequentially. You can pass other `harbor run` options after the task number. To check that a task can be loaded without running it:

```sh
./scripts/run-benchmark.sh 000 -a nop --dry-run
```

Harbor writes results under `jobs/`. A full trial can take more than an hour.

## Tasks

| # | Incident |
|---|---|
| 000 | [Frappe: deletes fail and background jobs stop](tasks/000--frappe--07-deletes-and-jobs-fail-194d9279/instruction.md) |
| 001 | [Frappe: busy traffic breaks Desk; jobs stay stuck](tasks/001--frappe--07-desk-and-queue-oom-0669b8e8/instruction.md) |
| 002 | [Frappe: busy periods knock users out of Desk](tasks/002--frappe--07-desk-and-queue-outage-64526986/instruction.md) |
| 003 | [Frappe: new records fail and jobs stop](tasks/003--frappe--07-new-records-and-jobs-fail-f3320b4c/instruction.md) |
| 004 | [Frappe: creating records fails; jobs stop](tasks/004--frappe--07-new-records-and-queue-oom-8ccfd9e7/instruction.md) |
| 005 | [Frappe: edits fail and background work stalls](tasks/07-writes-and-queue-oom-f1db8f42/instruction.md) |
| 006 | [Saleor: intermittent checkout timeouts](tasks/006--saleor-spine--10-T1-statement-timeout-canary-c7dcd6d4/instruction.md) |
| 007 | [Slack: concurrent sends become unreliable](tasks/007--slack-spine--06-F3-split-sequencer-eef5b438/instruction.md) |
| 008 | [Slack: message writes slow during recurring peaks](tasks/008--slack-spine--06-F4-maintenance-collision-cc222870/instruction.md) |
| 009 | [Slack: logins, unread counts, and sends slow](tasks/009--slack-spine--06-logins-unread-and-sends-all-slow-since-noon-16e3fd23/instruction.md) |
| 010 | [Slack: logins, unread counts, and sends slower](tasks/010--slack-spine--06-logins-unread-sends-slower-cccddfb2/instruction.md) |
| 011 | [Slack: sends fail and sign-ins slow](tasks/011--slack-spine--06-sends-crawl-then-store-slows-3e65c480/instruction.md) |
| 012 | [Slack: message sends stop working](tasks/012--slack-spine--06-sends-fail-after-strict-mode-plausible-pool-d18e8739/instruction.md) |
| 013 | [Slack: message sends stop working](tasks/013--slack-spine--06-sends-fail-after-strict-mode-turns-on-728f0739/instruction.md) |
| 014 | [Slack: sends fail during a compliance window](tasks/014--slack-spine--06-sends-fail-during-compliance-window-1f4b1235/instruction.md) |
| 015 | [Slack: message sends stop working](tasks/015--slack-spine--06-sends-fail-strict-pool-16-66fa1a7c/instruction.md) |
| 016 | [Slack: sends stall during busy periods](tasks/016--slack-spine--06-sends-slow-and-stall-every-minute-83867383/instruction.md) |
| 017 | [Slack: recurring stalls, then every send slows](tasks/017--slack-spine--06-stall-every-minute-and-later-every-send-crawls-bd50bae9/instruction.md) |
| 018 | [Slack: delivery is flaky under normal traffic](tasks/018--slack-spine--09-I1-seq-lock-leak-0b7c2973/instruction.md) |
| 019 | [Slack: delivery stays flaky despite normal traffic](tasks/019--slack-spine--13-P1-distractor-volume-shell-f73987d7/instruction.md) |

## License

Apache 2.0. Vendored upstream components retain their own notices in the task directories.
