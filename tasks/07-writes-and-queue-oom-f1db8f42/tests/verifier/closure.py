"""Exact task-shipped and fingerprinted verifier source closure."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .challenge_types import challenge_type

# Repository location of this package. Fingerprint paths are repository-relative
# and are recorded in calibration/qualification evidence, so they are derived
# from one constant rather than spelled out at each site.
PACKAGE_ROOT = "verifier"

_COMMON = (
    "__init__.py",
    "assessment.py",
    "checks.py",
    "challenge_types.py",
    "closure.py",
    "contract.py",
    "episode.py",
    "errors.py",
    "evaluate.py",
    "evidence.py",
    "health_bands.py",
    "materializers/__init__.py",
    "report.py",
    "reward.py",
    # evaluate.py imports run_task_verifier at module scope, so every bundle
    # needs this whether or not the contract declares a task_verifier.
    "task_verifier_runtime.py",
    # Every grading module validates some textual shape, so the explicit parsers
    # they replaced ``re`` with belong in every bundle.
    "textual.py",
)


def selected_source_relpaths(contract: Any) -> tuple[str, ...]:
    selected = set(_COMMON)
    if contract.materializers:
        selected.add("materializers/common.py")
    for materializer in contract.materializers:
        selected.add(f"materializers/{materializer}.py")
        if materializer == "outcome":
            selected.update(
                {
                    "health_bands.py",
                    "providers/__init__.py",
                    "providers/outcome.py",
                }
            )
        elif materializer == "service_identity":
            selected.update({"providers/__init__.py", "providers/service_identity.py"})
        elif materializer == "mariadb_state":
            selected.update({"providers/__init__.py", "providers/mariadb_state.py"})
        elif materializer == "redis_state":
            selected.update({"providers/__init__.py", "providers/redis_state.py"})
        elif materializer == "temporal_recurrence":
            selected.update(
                {
                    "providers/__init__.py",
                    "providers/temporal.py",
                    "providers/worker_policy_survivor.py",
                }
            )
    challenge = contract.challenge
    if challenge is not None:
        selected.add("challenge.py")
        if challenge.get("profile_id") == "slack_runtime_restart_v1":
            selected.add("providers/__init__.py")
        if challenge.get("profile_id") == "slack_sequence_restart_v1":
            selected.add("sequence_survivor.py")
        selected.update(
            {
                "profiles/__init__.py",
                f"profiles/{challenge['profile_id']}.py",
            }
        )
        spec = challenge_type(challenge["type"])
        if spec.broker_required:
            selected.update({"broker.py", "db_survivor.py", "restart_survivor.py"})
        if challenge["type"] in {"database_checkpoint", "database_survival"}:
            selected.add("db_survivor.py")
        elif challenge["type"] == "concurrent_sequence":
            selected.update({"db_survivor.py", "sequence_survivor.py"})
        elif challenge["type"] == "service_event_recurrence":
            selected.update(
                {"db_survivor.py", "restart_survivor.py", "service_event.py"}
            )
    return tuple(sorted(selected))


def selected_source_pairs(contract: Any) -> tuple[tuple[str, str], ...]:
    """Return task destination/repository source pairs for the shipped closure.

    Every task ships the current grader bytes. Declaring ``task_verifier`` adds
    the per-task runtime on top; it no longer selects a different evaluator.
    """

    return tuple(
        (destination, destination)
        for destination in selected_source_relpaths(contract)
    )


def selected_external_sources(contract: Any) -> tuple[tuple[str, str], ...]:
    """Task destination/repository source pairs for sources outside this package.

    The outcome, MariaDB/Redis-state, and temporal providers used to be listed here,
    vendored under declared renames from a separate tree. They now live in this
    package under their own names, so they are ordinary members of the internal
    closure above and need no declared rename. What remains
    genuinely external is the loadgen session planner, which the runtime-restart
    profile replays against and which is owned by ``loadgen-common``.
    """

    selected: list[tuple[str, str]] = []
    if (
        contract.challenge is not None
        and contract.challenge.get("profile_id") == "slack_runtime_restart_v1"
    ):
        selected.append(
            ("providers/session_planner.py", "loadgen-common/loadgen/session.py")
        )
    return tuple(selected)


def selected_fingerprint_relpaths(contract: Any) -> tuple[str, ...]:
    """Repository-relative files that determine the task's grader semantics."""

    selected = {
        f"{PACKAGE_ROOT}/{source}"
        for _destination, source in selected_source_pairs(contract)
    }
    selected.update(
        {
            "tools/stock_harbor_template.py",
            f"{PACKAGE_ROOT}/stock_harbor.py",
        }
    )
    if contract.challenge is not None:
        selected.add(f"{PACKAGE_ROOT}/challenge_generation.py")
    selected.update(source for _destination, source in selected_external_sources(contract))
    return tuple(sorted(selected))


def selected_fingerprint_sources(contract: Any) -> tuple[tuple[str, str], ...]:
    """Return stable logical names paired with their repository source paths."""

    selected = [
        (f"{PACKAGE_ROOT}/{destination}", f"{PACKAGE_ROOT}/{source}")
        for destination, source in selected_source_pairs(contract)
    ]
    selected.extend(
        (path, path)
        for path in (
            "tools/stock_harbor_template.py",
            f"{PACKAGE_ROOT}/stock_harbor.py",
        )
    )
    if contract.challenge is not None:
        selected.append(
            (
                f"{PACKAGE_ROOT}/challenge_generation.py",
                f"{PACKAGE_ROOT}/challenge_generation.py",
            )
        )
    selected.extend(
        (source, source) for _destination, source in selected_external_sources(contract)
    )
    return tuple(sorted(selected))


def stage_selected_sources(contract: Any, package: Path) -> None:
    """Stage the exact task-shipped closure for host challenge execution."""

    source_root = Path(__file__).resolve().parent
    for destination, source_relpath in selected_source_pairs(contract):
        source = source_root / source_relpath
        if not source.is_file():
            raise RuntimeError(f"selected verifier host source is missing: {source}")
        target = package / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    repo_root = source_root.parent
    for destination, relpath in selected_external_sources(contract):
        source = repo_root / relpath
        if not source.is_file():
            raise RuntimeError(
                f"selected verifier host external source is missing: {source}"
            )
        target = package / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
