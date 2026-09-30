"""Substrate-neutral loader for one task-owned, hashed verifier package."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .contract import VerificationContract
from .errors import EvidenceError
from .report import dump_json


def generated_task_verifier_root(
    ground_truth: Path, contract: VerificationContract
) -> Path | None:
    """Resolve the hidden package from the substrate-independent task layout."""

    if contract.task_verifier is None:
        return None
    ground_truth = Path(ground_truth)
    if (
        ground_truth.name != "ground-truth.yaml"
        or ground_truth.parent.name != "chart"
        or ground_truth.parent.parent.name != "environment"
    ):
        raise EvidenceError(
            "cannot resolve generated task root from ground truth: "
            f"{ground_truth}"
        )
    root = ground_truth.parents[2] / "tests" / "verifier" / "task_verifier"
    validate_task_verifier_package(root, contract)
    return root


def validate_task_verifier_package(
    root: Path, contract: VerificationContract
) -> dict[str, Path]:
    if contract.task_verifier is None:
        raise EvidenceError("task verifier package was provided without a task contract")
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise EvidenceError(f"task verifier package is missing or unsafe: {root}")
    declared = contract.task_verifier["files"]
    observed: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise EvidenceError(f"task verifier package contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        observed[relative] = path
    if set(observed) != set(declared):
        raise EvidenceError(
            "task verifier package inventory mismatch: "
            f"missing={sorted(set(declared) - set(observed))} "
            f"extra={sorted(set(observed) - set(declared))}"
        )
    for relative, path in observed.items():
        digest = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        if digest != declared[relative]:
            raise EvidenceError(
                f"task verifier digest mismatch for {relative}: "
                f"expected {declared[relative]}, observed {digest}"
            )
    return observed


def run_task_verifier(
    contract: VerificationContract,
    manifest: dict[str, Any],
    run_dir: Path,
    *,
    package_root: Path | None = None,
) -> None:
    if contract.task_verifier is None:
        return
    root = (
        Path(package_root)
        if package_root is not None
        else Path(__file__).resolve().parent / "task_verifier"
    )
    files = validate_task_verifier_package(root, contract)
    entrypoint = contract.task_verifier["entrypoint"]
    source = files[entrypoint]
    package_digest = hashlib.sha256(
        "\n".join(
            f"{path}:{digest}"
            for path, digest in contract.task_verifier["files"].items()
        ).encode()
    ).hexdigest()
    package_name = f"_sre_task_verifier_{package_digest[:16]}"
    module_name = f"{package_name}.{entrypoint.removesuffix('.py')}"
    package = ModuleType(package_name)
    package.__path__ = [str(root)]  # type: ignore[attr-defined]
    package.__package__ = package_name
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise EvidenceError(f"cannot load task verifier entrypoint: {entrypoint}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    before = _snapshot_run_dir(run_dir)
    try:
        spec.loader.exec_module(module)
        evaluate = module.evaluate
        payload = evaluate(run_dir, manifest)
    except (Exception, SystemExit) as exc:
        raise EvidenceError(f"task verifier execution failed: {entrypoint}: {exc}") from exc
    finally:
        for loaded in tuple(sys.modules):
            if loaded == package_name or loaded.startswith(f"{package_name}."):
                del sys.modules[loaded]
    after = _snapshot_run_dir(run_dir)
    if after != before:
        raise EvidenceError("task verifier mutated protected runtime evidence")
    if not isinstance(payload, dict) or not isinstance(payload.get("pass"), bool):
        raise EvidenceError("task verifier returned malformed evidence")
    derived = Path(run_dir) / "derived"
    derived.mkdir(exist_ok=True)
    (derived / "task-verifier.json").write_text(dump_json(payload))


def _snapshot_run_dir(run_dir: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(Path(run_dir).rglob("*")):
        if path.is_symlink():
            raise EvidenceError(f"protected evidence contains a symlink: {path}")
        if path.is_file():
            snapshot[path.relative_to(run_dir).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return snapshot
