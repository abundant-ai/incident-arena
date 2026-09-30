"""Evaluation of requirement-linked deterministic checks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .challenge_types import CHALLENGE_PROFILE_TYPES, CHALLENGE_TYPES
from .errors import ContractError, EvidenceError
from .evidence import EvidenceRef, EvidenceStore, parse_ref
from .textual import is_challenge_id, is_dotted_name, is_hex_digest


_RECEIPT_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "challenge_id",
        "actor",
        "type",
        "profile_id",
        "pass",
        "protected_bundle",
        "agent_frozen",
    }
)
_FAILURE_KEYS = _RECEIPT_ENVELOPE_KEYS | {"safe_repair_failure"}
_SANITIZED_SCOPE_KEYS = _FAILURE_KEYS | {"data"}
_RESTART_EVIDENCE_KEYS = frozenset(
    {
        "target_pod",
        "target_service",
        "ready",
        "replayed",
        "old_pod",
        "new_pod",
        "pre_restart",
        "post_restart",
        "traffic",
        "data",
        "image_identity_unchanged",
    }
)


@dataclass(frozen=True)
class ProtectedReceipt:
    """A verifier-owned challenge receipt with a recognized outcome shape."""

    variant: str
    failure: dict[str, str] | None = None


def _challenge_receipt_identity_matches(
    document: dict[str, Any], artifact: str
) -> bool:
    challenge_type = document.get("type")
    profile_id = document.get("profile_id")
    return (
        isinstance(challenge_type, str)
        and challenge_type in CHALLENGE_TYPES
        and isinstance(profile_id, str)
        and CHALLENGE_PROFILE_TYPES.get(profile_id) == challenge_type
        and artifact
        == f"challenge/{CHALLENGE_TYPES[challenge_type].receipt_name}"
    )


def _proven_frozen(frozen: Any) -> bool:
    """Recognize the freeze proof ``challenge._prove_frozen`` emits.

    Both endings carry the same key set. The protected proof normalizes a
    verified deadline freeze to the same clean Boolean boundary used by a
    declaration, while refusing any explicit unsafe mutation signal.
    """

    return (
        isinstance(frozen, dict)
        and set(frozen)
        == {
            "success",
            "remaining_pids",
            "declared",
            "submission_to_freeze_mutation",
            "receipt_sha256",
        }
        and frozen["success"] is True
        and frozen["remaining_pids"] == []
        and isinstance(frozen["declared"], bool)
        and frozen["submission_to_freeze_mutation"] is False
        and is_hex_digest(frozen["receipt_sha256"])
    )


def _bundle_binding_valid(document: dict[str, Any]) -> bool:
    bundle = document.get("protected_bundle")
    challenge_id = document.get("challenge_id")
    return (
        is_challenge_id(challenge_id)
        and isinstance(bundle, dict)
        and set(bundle) == {"sha256", "bytes"}
        and is_hex_digest(bundle["sha256"])
        and challenge_id == f"bundle-{bundle['sha256'][:24]}"
        and isinstance(bundle["bytes"], int)
        and not isinstance(bundle["bytes"], bool)
        and bundle["bytes"] > 0
    )


def _safe_repair_failure(value: Any) -> dict[str, str] | None:
    if (
        not isinstance(value, dict)
        or set(value) != {"type", "message"}
        or value["type"] != "SafeRepairFailure"
        or not isinstance(value["message"], str)
        or not value["message"]
    ):
        return None
    return value


def _setting_names(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(is_dotted_name(name) for name in value)
        and value == sorted(set(value))
    )


def _sanitized_scope_data(value: Any) -> bool:
    """Recognize the setting-name-only scope failure emitted by challenge.py."""

    if (
        not isinstance(value, dict)
        or set(value) != {"pass", "scope"}
        or value["pass"] is not False
        or not isinstance(value["scope"], dict)
    ):
        return False
    scope = value["scope"]
    if (
        set(scope)
        != {
            "pass",
            "hard_scope_unchanged",
            "unclassified_role_database_unchanged",
            "drift",
            "out_of_envelope",
        }
        or scope["pass"] is not False
        or scope["hard_scope_unchanged"] is not False
        or type(scope["unclassified_role_database_unchanged"]) is not bool
    ):
        return False
    drift = scope["drift"]
    out = scope["out_of_envelope"]
    if (
        not isinstance(drift, dict)
        or set(drift) != {"repair", "diagnostic", "protected"}
        or not all(_setting_names(drift[name]) for name in drift)
        or not isinstance(out, dict)
        or set(out)
        != {
            "protected",
            "repair_outside_app_scope",
            "unclassified_role_database",
            "unattributed_hard_change",
        }
        or not _setting_names(out["protected"])
        or not _setting_names(out["repair_outside_app_scope"])
        or type(out["unclassified_role_database"]) is not bool
        or type(out["unattributed_hard_change"]) is not bool
    ):
        return False
    return any(
        (
            out["protected"],
            out["repair_outside_app_scope"],
            out["unclassified_role_database"],
            out["unattributed_hard_change"],
        )
    )


def classify_protected_receipt(
    document: Any, artifact: str
) -> ProtectedReceipt | None:
    """Classify strict success and safe-failure challenge receipt variants.

    Valid failure receipts may omit success-only evidence. Those missing
    pointers are normal failed checks. A successful, forged, or malformed
    receipt still owes every pointer the contract observes and therefore raises
    the original evidence error.
    """

    if (
        not artifact.startswith("challenge/")
        or artifact.count("/") != 1
        or not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("actor") != "verifier"
        or not _challenge_receipt_identity_matches(document, artifact)
        or not _bundle_binding_valid(document)
        or not _proven_frozen(document.get("agent_frozen"))
        or not _RECEIPT_ENVELOPE_KEYS <= set(document)
    ):
        return None
    keys = set(document)
    if "safe_repair_failure" not in document:
        return ProtectedReceipt("success") if document["pass"] is True else None
    failure = _safe_repair_failure(document["safe_repair_failure"])
    if failure is None or document["pass"] is not False:
        return None
    if keys == _FAILURE_KEYS:
        return ProtectedReceipt("generic_failure", failure=failure)
    if (
        document["type"] == "fixed_pod_restart"
        and keys == _SANITIZED_SCOPE_KEYS
        and _sanitized_scope_data(document["data"])
    ):
        return ProtectedReceipt("sanitized_scope_failure", failure=failure)
    if (
        document["type"] == "fixed_pod_restart"
        and keys == _FAILURE_KEYS | _RESTART_EVIDENCE_KEYS
    ):
        return ProtectedReceipt("generic_failure", failure=failure)
    return None


def _ordered_set(value: Any, *, where: str) -> set[Any]:
    if not isinstance(value, list):
        raise EvidenceError(f"{where}: set comparison requires a JSON list")
    try:
        return set(value)
    except TypeError as exc:
        raise EvidenceError(f"{where}: set comparison requires scalar list items") from exc


def assert_value(actual: Any, assertion: dict[str, Any], *, where: str) -> bool:
    op = assertion["op"]
    expected = assertion.get("value")
    try:
        if op == "equals":
            return actual == expected
        if op == "not_equals":
            return actual != expected
        if op == "truthy":
            return bool(actual)
        if op == "falsy":
            return not bool(actual)
        if op == "empty":
            return len(actual) == 0
        if op == "not_empty":
            return len(actual) > 0
        if op == "gte":
            return actual >= expected
        if op == "lte":
            return actual <= expected
        if op == "gt":
            return actual > expected
        if op == "lt":
            return actual < expected
        if op == "contains_all":
            return _ordered_set(expected, where=where) <= _ordered_set(actual, where=where)
        if op == "set_equals":
            return _ordered_set(actual, where=where) == _ordered_set(expected, where=where)
        if op == "subset_of":
            return _ordered_set(actual, where=where) <= _ordered_set(expected, where=where)
        if op == "all_true":
            if not isinstance(actual, list):
                raise EvidenceError(f"{where}: all_true requires a JSON list")
            return bool(actual) and all(item is True for item in actual)
        if op == "length_gte":
            return len(actual) >= expected
        if op == "length_lte":
            return len(actual) <= expected
    except (TypeError, ValueError) as exc:
        raise EvidenceError(
            f"{where}: cannot apply assertion {op!r} to observed value {actual!r}"
        ) from exc
    raise ContractError(f"{where}: unsupported assertion operator {op!r}")


def evaluate_check(
    check: dict[str, Any],
    *,
    gate: str,
    namespace: str,
    store: EvidenceStore,
) -> dict[str, Any]:
    raw_id = check["id"]
    check_id = f"{gate}.{namespace}.{raw_id}" if namespace else f"{gate}.{raw_id}"
    observe = parse_ref(check["observe"], where=f"{check_id}.observe")
    extra_supporting: list[EvidenceRef] = []
    for index, raw_ref in enumerate(check.get("evidence", [])):
        ref = parse_ref(raw_ref, where=f"{check_id}.evidence[{index}]")
        if ref != observe and ref not in extra_supporting:
            extra_supporting.append(ref)
    try:
        actual = store.resolve(observe, where=f"{check_id}.observe")
    except EvidenceError:
        document = store.read_document(observe.artifact)
        receipt = classify_protected_receipt(document, observe.artifact)
        if (
            receipt is None
            or receipt.variant == "success"
            or receipt.failure is None
        ):
            raise
        for index, ref in enumerate(extra_supporting):
            if ref.artifact != observe.artifact:
                store.validate(ref, where=f"{check_id}.evidence[{index}]")
        fallback_ref = EvidenceRef(
            artifact=observe.artifact, pointer="/safe_repair_failure"
        )
        return {
            "id": check_id,
            "gate": gate,
            "requirement_ids": list(check["requirement_ids"]),
            "summary": check["summary"],
            "expected": check["expected"],
            "observed": {
                "value": None,
                "requested_pointer": observe.pointer,
                "receipt_variant": receipt.variant,
                "safe_repair_failure": receipt.failure,
            },
            "pass": False,
            "evidence": [
                fallback_ref.as_dict(),
                *(
                    ref.as_dict()
                    for ref in extra_supporting
                    if ref.artifact != observe.artifact
                ),
            ],
        }
    for index, ref in enumerate(extra_supporting):
        store.validate(ref, where=f"{check_id}.evidence[{index}]")
    supporting: list[EvidenceRef] = [observe, *extra_supporting]
    passed = assert_value(actual, check["assert"], where=check_id)
    return {
        "id": check_id,
        "gate": gate,
        "requirement_ids": list(check["requirement_ids"]),
        "summary": check["summary"],
        "expected": check["expected"],
        "observed": {"value": actual},
        "pass": passed,
        "evidence": [ref.as_dict() for ref in supporting],
    }


def reasons_for(checks: Iterable[dict[str, Any]]) -> list[str]:
    return [f"{check['id']}: {check['summary']}" for check in checks if not check["pass"]]
