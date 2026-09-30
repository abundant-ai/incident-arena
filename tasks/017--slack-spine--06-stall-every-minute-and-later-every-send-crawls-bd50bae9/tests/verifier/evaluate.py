"""Composable outcome-SLI and safe-repair verifier CLI.

The two gates consume DISJOINT semantic evidence. ``outcome`` is the
client-measured service-level story; ``safe_repair`` is the "did the repair stay
inside its blast radius" story. Neither reads the incident report: the report is
an advisory artifact with no path to reward, and no lifecycle authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .assessment import build_report_assessment
from .checks import evaluate_check, reasons_for
from .contract import VerificationContract, load_contract
from .errors import ContractError, EvidenceError, VerifierError
from .evidence import EvidenceStore, parse_ref
from .materializers import run_materializers
from .report import build_deterministic_report, dump_json, render_text
from .task_verifier_runtime import run_task_verifier

_OUTPUTS = (
    "verdict.json",
    "deterministic-report.json",
    "deterministic-report.txt",
    "report-assessment.json",
)


def _read_json(path: Path, *, required: bool) -> Any:
    if not path.is_file():
        if required:
            raise EvidenceError(f"required JSON artifact is missing: {path.name}")
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"malformed JSON artifact {path.name}: {exc}") from exc


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"v2 manifest is missing: {path}")
    try:
        manifest = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ContractError(f"v2 manifest is malformed YAML: {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ContractError(f"v2 manifest must be a mapping: {path}")
    return manifest


def _evaluate_outcome(contract: VerificationContract, store: EvidenceStore) -> dict[str, Any]:
    checks = [
        evaluate_check(check, gate="outcome", namespace="", store=store)
        for check in contract.outcome_checks
    ]
    by_id = {check["id"]: check for check in checks}
    if len(by_id) != len(checks):
        raise ContractError("outcome check IDs collide after namespacing")
    # Every outcome check gates. An empty conjunction would score PASS for doing
    # nothing.
    if not checks:
        raise ContractError(
            "outcome gate has no checks, so this contract is not gradeable"
        )
    return {
        "pass": all(check["pass"] for check in checks),
        "checks": dict(sorted(by_id.items())),
        "reasons": reasons_for(checks),
    }


def _evaluate_safe_repair(
    contract: VerificationContract, store: EvidenceStore
) -> dict[str, Any]:
    packs: dict[str, dict[str, Any]] = {}
    for pack_name in sorted(contract.packs):
        checks = [
            evaluate_check(
                check, gate="safe_repair", namespace=pack_name, store=store
            )
            for check in contract.packs[pack_name]["checks"]
        ]
        by_id = {check["id"]: check for check in checks}
        if len(by_id) != len(checks):
            raise ContractError(f"safe-repair pack {pack_name} has colliding check IDs")
        packs[pack_name] = {
            "pass": all(check["pass"] for check in checks),
            "checks": dict(sorted(by_id.items())),
            "reasons": reasons_for(checks),
        }

    common_pass = all(packs[name]["pass"] for name in contract.required_packs)
    envelopes: dict[str, dict[str, Any]] = {}
    for envelope in contract.envelopes:
        passed = all(packs[name]["pass"] for name in envelope["require"])
        envelopes[envelope["id"]] = {
            "pass": passed,
            "required_packs": list(envelope["require"]),
        }
    envelope_pass = not envelopes or any(item["pass"] for item in envelopes.values())
    # Safe repair answers exactly one question: did whatever the agent did stay
    # inside the blast radius the contract permits? A run that changed nothing
    # changed nothing unsafely, so it passes this gate on its own merits. That
    # is not a scoring loophole because ``overall`` is still the conjunction
    # with the outcome SLIs, which an unrepaired fault fails.
    passed = common_pass and envelope_pass
    reasons: list[str] = []
    for name in contract.required_packs:
        reasons.extend(packs[name]["reasons"])
    if envelopes and not envelope_pass:
        reasons.append("no declared legitimate repair envelope passed")
    return {
        "pass": passed,
        "packs": packs,
        "required_packs": list(contract.required_packs),
        "envelopes": envelopes,
        "reasons": reasons,
    }


def _diagnostics(
    contract: VerificationContract,
    *,
    store: EvidenceStore,
    report_path: Path,
    submitted: bool,
) -> dict[str, Any]:
    # The report is advisory. It is fingerprinted here so a reader can tell one
    # narrative from another, and it is deliberately absent from every gate.
    result: dict[str, Any] = {
        "report": {
            "submitted": submitted,
            "sha256": (
                hashlib.sha256(report_path.read_bytes()).hexdigest()
                if report_path.is_file()
                else None
            ),
        },
        "mutations": {},
        "restarts": {},
        "interventions": {},
    }
    configured = contract.diagnostics.get("artifacts", {})
    for name in ("mutations", "restarts", "interventions"):
        if name not in configured:
            continue
        ref = parse_ref(configured[name], where=f"verification.diagnostics.artifacts.{name}")
        try:
            result[name] = store.resolve(ref, where=f"diagnostics.{name}")
        except EvidenceError as exc:
            # Diagnostics describe a verdict; they must never manufacture one.
            # Retain the failure explicitly so missing diagnostic evidence is
            # visible without converting an already-known PASS/FAIL into an
            # infrastructure error.
            result[name] = {
                "status": "unavailable",
                "reason": str(exc),
                "source": ref.as_dict(),
            }
    return result


def evaluate_run(
    run_dir: Path,
    manifest_path: Path,
    *,
    judge_result: Any = None,
    write_artifacts: bool = True,
    task_verifier_root: Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise EvidenceError(f"v2 run directory does not exist: {run_dir}")
    manifest = _load_manifest(Path(manifest_path))
    contract = load_contract(manifest)

    report_path = run_dir / "report.json"
    # Advisory artifact: an absent report is a legitimate episode, a malformed
    # one is still an evidence error. Nothing downstream may gate on either.
    report = _read_json(report_path, required=False)
    submitted = report is not None
    run_materializers(contract, manifest, run_dir)
    run_task_verifier(
        contract,
        manifest,
        run_dir,
        package_root=task_verifier_root,
    )
    store = EvidenceStore(run_dir)

    outcome = _evaluate_outcome(contract, store)
    safe = _evaluate_safe_repair(contract, store)
    overall_pass = outcome["pass"] and safe["pass"]
    reasons = list(outcome["reasons"]) + list(safe["reasons"])
    verdict = {
        "schema_version": 2,
        "outcome": outcome,
        "safe_repair": safe,
        "diagnostics": _diagnostics(
            contract, store=store, report_path=report_path, submitted=submitted
        ),
        "overall": "PASS" if overall_pass else "FAIL",
        "reasons": reasons,
    }
    deterministic = build_deterministic_report(verdict, run_dir)
    assessment = build_report_assessment(
        report_submitted=submitted,
        deterministic_report=deterministic,
        enabled=contract.report_assessment["enabled"],
        judge_required=contract.report_assessment["judge_required"],
        judge_result=judge_result,
    )
    if write_artifacts:
        (run_dir / "verdict.json").write_text(dump_json(verdict))
        (run_dir / "deterministic-report.json").write_text(dump_json(deterministic))
        (run_dir / "deterministic-report.txt").write_text(render_text(deterministic))
        (run_dir / "report-assessment.json").write_text(dump_json(assessment))
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a protected verifier rundir")
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    for name in _OUTPUTS:
        path = args.run / name
        if path.exists():
            path.unlink()
    try:
        verdict = evaluate_run(args.run, args.manifest)
    except VerifierError as exc:
        print(f"verifier: {exc}", file=sys.stderr)
        return 2
    print(dump_json(verdict), end="")
    return 0 if verdict["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
