"""Shared completed-episode lifecycle validation.

The report declaration and the protected soak are independent facts.  A
completed D28 episode always has a finite soak boundary; ``declare_ts_s`` alone
distinguishes a reported repair from window expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class EpisodeCompletion:
    """Validated timestamps and declaration state for a completed episode."""

    declared: bool
    declare_ts_s: float | None
    soak_start_s: float
    end_s: float


def _finite_time(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{where} must be a finite non-negative number, got {value!r}")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise RuntimeError(f"{where} must be a finite non-negative number, got {value!r}")
    return result


def validate_episode_metadata(
    payload: Any,
    *,
    label: str = "episode metadata",
) -> EpisodeCompletion:
    """Validate the lifecycle fields shared by ``meta`` and ``episode_done``.

    This deliberately fails closed on the old ``null``/``null`` sentinel.  It
    represented an incomplete run, not a completed undeclared episode.
    """

    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object, got {payload!r}")
    if payload.get("error"):
        raise RuntimeError(f"{label} reports an error: {payload['error']!r}")

    declare_raw = payload.get("declare_ts_s")
    declare_ts_s = (
        None
        if declare_raw is None
        else _finite_time(declare_raw, f"{label}.declare_ts_s")
    )
    soak_start_s = _finite_time(payload.get("soak_start_s"), f"{label}.soak_start_s")
    end_s = _finite_time(payload.get("end_s"), f"{label}.end_s")
    if end_s < soak_start_s:
        raise RuntimeError(
            f"{label}.end_s must be >= soak_start_s, got "
            f"{end_s!r} < {soak_start_s!r}"
        )

    completion_reason = payload.get("completion_reason")
    if completion_reason is not None:
        expected_reason = (
            "declared_soak_complete"
            if declare_ts_s is not None
            else "window_elapsed_soak_complete"
        )
        if completion_reason != expected_reason:
            raise RuntimeError(
                f"{label}.completion_reason={completion_reason!r} contradicts "
                f"declare_ts_s; expected {expected_reason!r}"
            )

    return EpisodeCompletion(
        declared=declare_ts_s is not None,
        declare_ts_s=declare_ts_s,
        soak_start_s=soak_start_s,
        end_s=end_s,
    )


def validate_episode_done(payload: Any) -> dict[str, Any]:
    """Validate a completed ``episode_done.json`` payload and return it."""

    if not isinstance(payload, dict):
        raise RuntimeError(
            "slack-spine verifier: episode_done.json is not a JSON "
            f"object: {payload!r}"
        )
    if payload.get("error"):
        raise RuntimeError(
            "slack-spine verifier: loadgen sidecar reported an error: "
            f"{payload['error']!r} (full payload: {payload!r})"
        )
    if payload.get("done") is not True:
        raise RuntimeError(
            "slack-spine verifier: episode_done.json does not describe a "
            f"completed episode: {payload!r}"
        )
    if payload.get("completion_reason") is None:
        raise RuntimeError(
            "slack-spine verifier: completed episode_done.json lacks "
            f"completion_reason: {payload!r}"
        )
    validate_episode_metadata(payload, label="episode_done.json")
    return payload
