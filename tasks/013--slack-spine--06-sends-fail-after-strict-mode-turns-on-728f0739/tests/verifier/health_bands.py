"""Repository-wide tolerance applied to existing generic SLI bands."""

from __future__ import annotations

RUN_TOLERANCE_VERSION = "reused-health-bands-v1-20pct"
CEILING_MULTIPLIER = 1.20
FLOOR_MULTIPLIER = 0.80


def relaxed_ceiling(value: float, *, maximum: float | None = None) -> float:
    result = value * CEILING_MULTIPLIER
    return min(maximum, result) if maximum is not None else result


def relaxed_floor(value: float, *, minimum: float = 0.0) -> float:
    return max(minimum, value * FLOOR_MULTIPLIER)
