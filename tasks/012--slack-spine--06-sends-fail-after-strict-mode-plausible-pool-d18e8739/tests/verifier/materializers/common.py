"""Shared strict readers for selected verifier materializers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..errors import EvidenceError


def read_json(path: Path, *, required: bool) -> Any:
    if not path.is_file():
        if required:
            raise EvidenceError(f"materializer required artifact is missing: {path.name}")
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"materializer JSON is malformed: {path}: {exc}") from exc


def read_jsonl(path: Path, *, required: bool) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            raise EvidenceError(f"materializer required artifact is missing: {path.name}")
        return []
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"malformed JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise EvidenceError(f"JSONL row is not a mapping at {path}:{line_number}")
        rows.append(row)
    return rows


def tree_hash(root: Path) -> str:
    if not root.is_dir():
        raise EvidenceError(f"required configuration evidence directory is missing: {root.name}")
    digest = hashlib.sha256()
    files = [path for path in sorted(root.rglob("*")) if path.is_file()]
    if not files:
        raise EvidenceError(f"configuration evidence directory is empty: {root.name}")
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def repair_declared(run_dir: Path) -> bool:
    """Did the agent explicitly declare its repair complete?

    The lifecycle fact lives in the loadgen's own metadata as ``declare_ts_s``,
    NOT in the presence of an incident report. Those two used to be the same
    event because ``submit_incident_report`` was what froze the episode; they are
    now separate commands, and a materializer that kept reading ``report.json``
    would be inferring a freeze boundary from an advisory artifact the agent can
    file, skip, or file late.

    A completed episode always has ``meta.json``. Fail loudly if it does not:
    guessing ``False`` would silently reclassify a declared repair as a deadline
    expiry and change which snapshots the safe-repair packs compare.
    """
    meta = read_json(run_dir / "meta.json", required=True)
    if not isinstance(meta, dict):
        raise EvidenceError("meta.json must contain a mapping")
    if "declare_ts_s" not in meta:
        raise EvidenceError(
            "meta.json has no declare_ts_s: the loadgen did not record whether "
            "the agent declared its repair complete"
        )
    return meta["declare_ts_s"] is not None
