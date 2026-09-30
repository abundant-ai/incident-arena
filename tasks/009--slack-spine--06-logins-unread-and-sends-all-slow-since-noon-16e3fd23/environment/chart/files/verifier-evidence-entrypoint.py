"""Durable verifier episode ownership for the pinned v13 loadgen image."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any


def _install_runtime_phase_capture(sidecar: Any) -> None:
    """Backport protected runtime snapshots into pinned loadgen images."""
    if hasattr(sidecar, "_capture_runtime_state_phase"):
        return

    def manifest() -> dict[str, Any]:
        if not sidecar.GROUND_TRUTH_PATH.is_file():
            raise RuntimeError("runtime-state phase capture requires the task answer key")
        value = sidecar.yaml.safe_load(sidecar.GROUND_TRUTH_PATH.read_text())
        if not isinstance(value, dict):
            raise RuntimeError("runtime-state phase manifest must be a mapping")
        return value

    async def capture(value: dict[str, Any], phase: str) -> dict[str, Any] | None:
        cfg = value.get("runtime_state")
        if cfg is None:
            return None
        if not isinstance(cfg, dict):
            raise RuntimeError("runtime_state must be a mapping")
        phases = cfg.get("capture_phases", [])
        if (
            not isinstance(phases, list)
            or any(item not in {"declaration", "soak_end"} for item in phases)
            or len(phases) != len(set(phases))
        ):
            raise RuntimeError(
                "runtime_state.capture_phases must be a unique list drawn from "
                "declaration and soak_end"
            )
        if phase not in phases:
            return None
        return await sidecar._probe_runtime_state_http(value)

    def add_snapshot(path: Any, value: dict[str, Any]) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"protected runtime-state snapshot is malformed: {path}")
        if "runtime_state" in payload:
            raise RuntimeError(f"protected runtime-state snapshot already exists: {path}")
        payload["runtime_state"] = value
        sidecar._write_json_atomic(path, payload)

    original_boundary_factory = sidecar.make_agent_boundary_hook

    def boundary_factory(lg: Any, state: dict[str, Any]):
        original_boundary = original_boundary_factory(lg, state)

        async def boundary(reason: str) -> None:
            await original_boundary(reason)
            value = await capture(manifest(), "declaration")
            if value is not None:
                add_snapshot(sidecar.CONFIG_AT_DECLARE_JSON, value)

        return boundary

    original_soak_snapshot = sidecar._snapshot_soak_end

    async def soak_snapshot(lg: Any) -> None:
        await original_soak_snapshot(lg)
        value = await capture(manifest(), "soak_end")
        if value is not None:
            add_snapshot(sidecar.CONFIG_AT_SOAK_END_JSON, value)

    sidecar.make_agent_boundary_hook = boundary_factory
    sidecar._snapshot_soak_end = soak_snapshot


def claim_episode(sidecar: Any) -> str:
    """Return new, finalized, or interrupted without replacing any evidence."""
    grader = sidecar.GRADER
    done = sidecar.EPISODE_DONE_JSON
    started = grader / ".episode-started.json"
    grader.mkdir(parents=True, exist_ok=True)
    if done.exists():
        receipt = json.loads(done.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or not isinstance(receipt.get("done"), bool):
            raise RuntimeError("persistent episode receipt has an invalid shape")
        return "finalized"
    try:
        fd = os.open(started, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return "interrupted"
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(
            {"started_at": datetime.now(timezone.utc).isoformat()},
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return "new"


async def _serve_only(sidecar: Any) -> None:
    state = {
        "lg": None,
        "grader_access_token": sidecar.load_grader_access_token(),
        "require_baseline": sidecar.SOURCE_SNAPSHOT_ENABLED,
        "baseline_ready": not sidecar.SOURCE_SNAPSHOT_ENABLED,
    }
    await sidecar.start_http_server(state)
    await sidecar._sleep_forever()


def main() -> None:
    import loadgen_sidecar as sidecar

    _install_runtime_phase_capture(sidecar)

    # Future images may own the same protocol natively. Do not claim their
    # marker before delegating or the native code would classify itself as a
    # restart.
    if hasattr(sidecar, "_claim_or_resume_episode"):
        sidecar.main()
        return

    state = claim_episode(sidecar)
    if state == "new":
        sidecar.main()
        return
    if state == "interrupted":
        error = (
            "InfrastructureEvidenceRestart: loadgen restarted while evidence "
            "collection was in flight; partial evidence was frozen and the "
            "episode was not rerun"
        )
        sidecar._write_episode_done(
            {
                "done": False,
                "error": error,
                "declare_ts_s": None,
                "soak_start_s": None,
                "end_s": None,
            }
        )
    asyncio.run(_serve_only(sidecar))


if __name__ == "__main__":
    main()
