"""Evaluate protected queue-Redis configuration as direct verifier evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import EvidenceError
from ..providers.redis_state import evaluate_redis_state, read_redis_state


def materialize(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        snapshots = read_redis_state(run_dir, manifest)
        result = evaluate_redis_state(snapshots, manifest)
    except Exception as exc:
        raise EvidenceError(
            f"redis_state materializer failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(result, dict) or not isinstance(result.get("pass"), bool):
        raise EvidenceError("redis_state materializer returned malformed evidence")
    return result
