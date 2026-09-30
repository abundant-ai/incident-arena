"""The verifier: one deterministic grading engine for every substrate.

``evaluate_run`` is a pure function of a captured rundir plus a scenario's
ground-truth manifest. There is NO LLM anywhere in the grading path (DECISIONS.md
D12). ``closure.py`` selects the exact per-task subset of this package that is
vendored into ``tasks/<substrate>/<id>/tests/verifier/`` and graded in-pod.

``providers/`` holds the reviewed SLA/state implementations a contract selects
by name; ``materializers/`` turns a rundir into the derived artifacts those
contracts observe. Neither is reachable except through a ``verification`` block,
and an absent block is a hard error.

Substrate-specific host verifiers (e.g. ``SlackSpineHostVerifier``) live with
their substrate under ``substrates/<name>/verifier/``; ``host.py`` adapts one
for host-side debugging.
"""

from .evaluate import evaluate_run
from .reward import rewards_from_verdict

__all__ = ["evaluate_run", "rewards_from_verdict"]
