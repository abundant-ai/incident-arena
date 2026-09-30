"""Derive a semantic configuration diff without embedding policy.

Allowed keys and numeric ceilings belong in public requirement-linked checks.
This materializer only turns protected snapshots into deterministic evidence.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from pathlib import Path
from typing import Any

import yaml

from ..errors import EvidenceError
from .common import repair_declared

_MISSING = object()


def _json_safe(value: Any, *, where: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceError(f"repair_scope contains a non-finite number at {where}")
        return value
    if isinstance(value, (dt.date, dt.datetime)):
        return {"yaml_type": type(value).__name__, "iso8601": value.isoformat()}
    if isinstance(value, list):
        return [
            _json_safe(child, where=f"{where}[{index}]")
            for index, child in enumerate(value)
        ]
    raise EvidenceError(
        f"repair_scope contains an unsupported YAML value at {where}: {type(value).__name__}"
    )


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        if not value:
            return {prefix: {}}
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, (str, int, float, bool)):
                raise EvidenceError("repair_scope configuration has a non-scalar key")
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict) and child:
                result.update(_flatten(child, path))
            else:
                result[path] = _json_safe(child, where=path)
        return result
    return {prefix: _json_safe(value, where=prefix or "<root>")}


def _tree(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise EvidenceError(f"repair_scope evidence directory is missing: {root.name}")
    files = [path for path in sorted(root.rglob("*")) if path.is_file()]
    if not files:
        raise EvidenceError(f"repair_scope evidence directory is empty: {root.name}")
    result: dict[str, Any] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() not in {".yaml", ".yml"}:
            raw = path.read_bytes()
            result[f"file:{relative}"] = {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
            continue
        try:
            document = yaml.safe_load(path.read_text())
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise EvidenceError(f"repair_scope cannot read YAML in {path}: {exc}") from exc
        prefix = f"yaml:{relative}"
        for key, value in _flatten({} if document is None else document, prefix).items():
            if key in result and result[key] != value:
                raise EvidenceError(
                    f"repair_scope duplicate dotted key has conflicting values: {key}"
                )
            result[key] = value
    return result


def _changed(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    return sorted(
        key
        for key in set(left) | set(right)
        if left.get(key, _MISSING) != right.get(key, _MISSING)
    )


def changed_config_keys(before: Path, after: Path) -> list[str]:
    """The dotted keys that differ between two config-snapshot directories.

    The same comparison ``materialize`` grades on, exposed so an evidence
    producer can be tested against the reader that will consume it.
    """
    return _changed(_tree(before), _tree(after))


def materialize(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    del manifest  # Policy is expressed by public checks, never hidden here.
    declared = repair_declared(run_dir)
    before = _tree(run_dir / "config_before")
    # The boundary snapshot: declaration-time for a run that declared, deadline
    # freeze time otherwise. Either way it is the last state the agent could
    # reach, which is what "scope of the repair" has to be measured against.
    declaration = _tree(run_dir / "config_after")
    # Every completed episode has a protected soak, including one that reached
    # its deadline undeclared. Missing soak evidence is an incomplete bundle and
    # must fail loudly rather than become a sentinel.
    soak = _tree(run_dir / "config_after_soak_end")
    return {
        "pass": True,
        "declared": declared,
        "changed_keys": _changed(before, declaration),
        "post_declaration_drift": _changed(declaration, soak),
        "phases": {
            "before": before,
            "declaration": declaration,
            "soak_end": soak,
        },
    }
