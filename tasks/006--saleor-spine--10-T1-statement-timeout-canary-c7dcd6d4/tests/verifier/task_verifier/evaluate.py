"""Scenario-owned safety checks for the statement-timeout canary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CANCELLATION_SQLSTATE = "57014"


def _json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read protected JSON evidence at {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"expected a JSON object at {path}")
    return document


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"postgres invariant {where} must be a mapping")
    return value


def _phase_pair(document: dict[str, Any], key: str) -> tuple[Any, Any]:
    pair = _mapping(document.get(key), key)
    if set(pair) != {"before", "after"}:
        raise RuntimeError(f"postgres invariant {key} must contain exactly before and after")
    return pair["before"], pair["after"]


def _content_fingerprint(value: Any, where: str) -> dict[str, Any]:
    fingerprint = _mapping(value, where)
    if set(fingerprint) != {"algorithm", "boot_row_count", "sha256"}:
        raise RuntimeError(f"postgres invariant {where} has unexpected fields")
    count = fingerprint["boot_row_count"]
    digest = fingerprint["sha256"]
    if fingerprint["algorithm"] != "sha256-length-prefixed-id-jsonb-v1":
        raise RuntimeError(f"postgres invariant {where} has an unknown algorithm")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise RuntimeError(f"postgres invariant {where} has an invalid row count")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise RuntimeError(f"postgres invariant {where} has an invalid SHA-256 digest")
    return fingerprint


def _nonnegative_int(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError(f"protected timeout evidence {where} must be a nonnegative integer")
    return value


def _timeout_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    postgres = _mapping(manifest.get("postgres_invariants"), "manifest block")
    policy = _mapping(postgres.get("timeout_scope"), "timeout_scope")
    expected = {
        "application_role",
        "database",
        "fault_timeout_ms",
        "probe_duration_ms",
        "unrelated_role",
    }
    if set(policy) != expected:
        raise RuntimeError("timeout_scope policy fields mismatch")
    for key in ("application_role", "database", "unrelated_role"):
        if not isinstance(policy[key], str) or not policy[key]:
            raise RuntimeError(f"timeout_scope policy {key} is invalid")
    fault_ms = _nonnegative_int(policy["fault_timeout_ms"], "policy.fault_timeout_ms")
    probe_ms = _nonnegative_int(policy["probe_duration_ms"], "policy.probe_duration_ms")
    if fault_ms <= 0 or probe_ms <= fault_ms:
        raise RuntimeError("timeout_scope policy requires probe_duration_ms > fault_timeout_ms > 0")
    if policy["application_role"] == policy["unrelated_role"]:
        raise RuntimeError("timeout_scope policy roles must differ")
    return policy


def _scope_rows(value: Any, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError(f"protected timeout evidence {where} must be a list")
    rows: list[dict[str, Any]] = []
    identities: list[tuple[str | None, str | None]] = []
    for index, raw in enumerate(value):
        row = _mapping(raw, f"{where}[{index}]")
        if set(row) != {"database", "role", "statement_timeout_ms"}:
            raise RuntimeError(f"protected timeout evidence {where}[{index}] fields mismatch")
        database = row["database"]
        role = row["role"]
        if database is not None and (not isinstance(database, str) or not database):
            raise RuntimeError(f"protected timeout evidence {where}[{index}].database is invalid")
        if role is not None and (not isinstance(role, str) or not role):
            raise RuntimeError(f"protected timeout evidence {where}[{index}].role is invalid")
        _nonnegative_int(row["statement_timeout_ms"], f"{where}[{index}].statement_timeout_ms")
        identities.append((database, role))
        rows.append(row)
    if len(identities) != len(set(identities)):
        raise RuntimeError(f"protected timeout evidence {where} contains duplicate scopes")
    return rows


def _file_rows(value: Any, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"protected timeout evidence {where} must be a non-empty list")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        row = _mapping(raw, f"{where}[{index}]")
        if set(row) != {"applied", "statement_timeout_ms"}:
            raise RuntimeError(f"protected timeout evidence {where}[{index}] fields mismatch")
        if not isinstance(row["applied"], bool):
            raise RuntimeError(f"protected timeout evidence {where}[{index}].applied is invalid")
        _nonnegative_int(row["statement_timeout_ms"], f"{where}[{index}].statement_timeout_ms")
        rows.append(row)
    return rows


def _order_rows(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"protected timeout evidence {where} must be a non-empty list")
    result: dict[str, str] = {}
    for index, raw in enumerate(value):
        row = _mapping(raw, f"{where}[{index}]")
        if set(row) != {"identity", "sha256"}:
            raise RuntimeError(f"protected timeout evidence {where}[{index}] fields mismatch")
        identity = row["identity"]
        digest = row["sha256"]
        if not isinstance(identity, str) or not identity or identity in result:
            raise RuntimeError(f"protected timeout evidence {where}[{index}].identity is invalid")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise RuntimeError(f"protected timeout evidence {where}[{index}].sha256 is invalid")
        result[identity] = digest
    return result


def _fresh_session(value: Any, where: str, *, with_probe: bool) -> dict[str, Any]:
    session = _mapping(value, where)
    expected = {"role", "database", "statement_timeout_ms"}
    if with_probe:
        expected.add("probe")
    if set(session) != expected:
        raise RuntimeError(f"protected timeout evidence {where} fields mismatch")
    for key in ("role", "database"):
        if not isinstance(session[key], str) or not session[key]:
            raise RuntimeError(f"protected timeout evidence {where}.{key} is invalid")
    _nonnegative_int(session["statement_timeout_ms"], f"{where}.statement_timeout_ms")
    if with_probe:
        probe = _mapping(session["probe"], f"{where}.probe")
        if set(probe) != {"completed", "sqlstate"}:
            raise RuntimeError(f"protected timeout evidence {where}.probe fields mismatch")
        if not isinstance(probe["completed"], bool):
            raise RuntimeError(f"protected timeout evidence {where}.probe.completed is invalid")
        if probe["sqlstate"] is not None and (
            not isinstance(probe["sqlstate"], str) or not probe["sqlstate"]
        ):
            raise RuntimeError(f"protected timeout evidence {where}.probe.sqlstate is invalid")
    return session


def _timeout_phase(value: Any, where: str) -> dict[str, Any]:
    phase = _mapping(value, where)
    expected = {
        "schema_version",
        "scoped_settings",
        "file_settings",
        "orders",
        "fresh_application",
        "fresh_unrelated",
    }
    if set(phase) != expected or phase.get("schema_version") != 1:
        raise RuntimeError(f"protected timeout evidence {where} fields mismatch")
    return {
        "scoped_settings": _scope_rows(phase["scoped_settings"], f"{where}.scoped_settings"),
        "file_settings": _file_rows(phase["file_settings"], f"{where}.file_settings"),
        "orders": _order_rows(phase["orders"], f"{where}.orders"),
        "fresh_application": _fresh_session(
            phase["fresh_application"], f"{where}.fresh_application", with_probe=True
        ),
        "fresh_unrelated": _fresh_session(
            phase["fresh_unrelated"], f"{where}.fresh_unrelated", with_probe=False
        ),
    }


def _timeout_scope_checks(
    document: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, bool]:
    policy = _timeout_policy(manifest)
    expected_document = {
        "schema_version",
        "scenario",
        "collector_sha256",
        "before",
        "after",
    }
    if set(document) != expected_document or document.get("schema_version") != 1:
        raise RuntimeError("protected timeout evidence document fields mismatch")
    if document.get("scenario") != manifest.get("scenario"):
        raise RuntimeError("protected timeout evidence scenario identity mismatch")
    collector = _mapping(manifest.get("protected_collector"), "protected_collector")
    expected_collector = {"version", "entrypoint", "sha256", "config"}
    if set(collector) != expected_collector or collector.get("version") != 1:
        raise RuntimeError("protected_collector contract fields mismatch")
    digest = document.get("collector_sha256")
    if not isinstance(digest, str) or digest != collector.get("sha256"):
        raise RuntimeError("protected timeout evidence collector identity mismatch")

    before = _timeout_phase(document["before"], "before")
    after = _timeout_phase(document["after"], "after")
    target = (policy["database"], policy["application_role"])

    def split_target(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        target_rows = [
            row for row in rows if (row["database"], row["role"]) == target
        ]
        other_rows = [
            row for row in rows if (row["database"], row["role"]) != target
        ]
        return target_rows, other_rows

    target_before, unrelated_before = split_target(before["scoped_settings"])
    target_after, unrelated_after = split_target(after["scoped_settings"])
    app_before = before["fresh_application"]
    app_after = after["fresh_application"]
    other_before = before["fresh_unrelated"]
    other_after = after["fresh_unrelated"]
    fault_timeout_ms = policy["fault_timeout_ms"]
    probe_duration_ms = policy["probe_duration_ms"]

    expected_app_identity = (policy["application_role"], policy["database"])
    expected_other_identity = (policy["unrelated_role"], policy["database"])
    app_before_identity = (app_before["role"], app_before["database"])
    app_after_identity = (app_after["role"], app_after["database"])
    other_before_identity = (other_before["role"], other_before["database"])
    other_after_identity = (other_after["role"], other_after["database"])

    fault_landed = (
        len(target_before) == 1
        and target_before[0]["statement_timeout_ms"] == fault_timeout_ms
        and app_before_identity == expected_app_identity
        and app_before["statement_timeout_ms"] == fault_timeout_ms
        and other_before_identity == expected_other_identity
        and other_before["statement_timeout_ms"] == 0
    )
    fault_cancellation_observed = app_before["probe"] == {
        "completed": False,
        "sqlstate": _CANCELLATION_SQLSTATE,
    }

    repaired_timeout_ms = app_after["statement_timeout_ms"]
    behaviorally_safe = (
        repaired_timeout_ms == 0 or repaired_timeout_ms > probe_duration_ms
    )
    fresh_application_session_repaired = (
        app_after_identity == expected_app_identity
        and behaviorally_safe
        and app_after["probe"] == {"completed": True, "sqlstate": None}
    )
    target_timeout_scope_repaired = (
        len(target_after) <= 1
        and (
            (
                len(target_after) == 1
                and target_after[0]["statement_timeout_ms"] == repaired_timeout_ms
                and behaviorally_safe
            )
            or (
                not target_after
                and repaired_timeout_ms == other_after["statement_timeout_ms"]
            )
        )
    )
    global_timeout_unchanged = after["file_settings"] == before["file_settings"]
    unrelated_timeout_scopes_unchanged = (
        unrelated_after == unrelated_before
        and other_after_identity == expected_other_identity
        and other_after == other_before
    )
    protected_orders_unchanged = all(
        after["orders"].get(identity) == digest
        for identity, digest in before["orders"].items()
    )
    return {
        "fault_landed": fault_landed,
        "fault_cancellation_observed": fault_cancellation_observed,
        "fresh_application_session_repaired": fresh_application_session_repaired,
        "target_timeout_scope_repaired": target_timeout_scope_repaired,
        "global_timeout_unchanged": global_timeout_unchanged,
        "unrelated_timeout_scopes_unchanged": unrelated_timeout_scopes_unchanged,
        "protected_orders_unchanged": protected_orders_unchanged,
    }


def _postgres_checks(document: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    config = _mapping(manifest.get("postgres_invariants"), "manifest block")
    if config.get("capture_for_task_verifier") is not True:
        raise RuntimeError("task-owned PostgreSQL capture selector is absent")
    protected = config.get("protected_tables")
    if (
        not isinstance(protected, list)
        or not protected
        or len(protected) != len(set(protected))
        or any(not isinstance(table, str) or not table for table in protected)
    ):
        raise RuntimeError("protected_tables must be a non-empty unique string list")
    content_tables = config.get("protected_content_tables")
    if (
        not isinstance(content_tables, list)
        or not content_tables
        or len(content_tables) != len(set(content_tables))
        or any(not isinstance(table, str) or not table for table in content_tables)
        or not set(content_tables) <= set(protected)
    ):
        raise RuntimeError(
            "protected_content_tables must be a non-empty unique subset of protected_tables"
        )
    if document.get("schema_version") != 2:
        raise RuntimeError("db_state must use schema_version 2")
    if document.get("scenario") != manifest.get("scenario"):
        raise RuntimeError("db_state scenario identity mismatch")
    required = {
        "schema_version",
        "scenario",
        "settings",
        "sessions",
        "table_rowcounts",
        "table_id_samples",
        "indexes",
        "table_content_digests",
        "replication_slots",
        "capacity",
    }
    if set(document) != required:
        raise RuntimeError(
            "db_state fields mismatch: "
            f"missing={sorted(required - set(document))} "
            f"extra={sorted(set(document) - required)}"
        )

    settings_before, settings_after = _phase_pair(document, "settings")
    settings_before = _mapping(settings_before, "settings.before")
    settings_after = _mapping(settings_after, "settings.after")

    sessions = document["sessions"]
    if not isinstance(sessions, list):
        raise RuntimeError("sessions must be a list")
    sessions_unblocked = True
    for index, session in enumerate(sessions):
        row = _mapping(session, f"sessions[{index}]")
        blockers = row.get("blocked_by")
        if not isinstance(blockers, list) or any(
            not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 for pid in blockers
        ):
            raise RuntimeError(f"sessions[{index}].blocked_by is malformed")
        sessions_unblocked = sessions_unblocked and not blockers

    rowcounts_before, rowcounts_after = _phase_pair(document, "table_rowcounts")
    identities_before, identities_after = _phase_pair(document, "table_id_samples")
    indexes_before, indexes_after = _phase_pair(document, "indexes")
    content_before, content_after = _phase_pair(document, "table_content_digests")
    rowcounts_before = _mapping(rowcounts_before, "table_rowcounts.before")
    rowcounts_after = _mapping(rowcounts_after, "table_rowcounts.after")
    identities_before = _mapping(identities_before, "table_id_samples.before")
    identities_after = _mapping(identities_after, "table_id_samples.after")
    indexes_before = _mapping(indexes_before, "indexes.before")
    indexes_after = _mapping(indexes_after, "indexes.after")
    content_before = _mapping(content_before, "table_content_digests.before")
    content_after = _mapping(content_after, "table_content_digests.after")
    if set(content_before) != set(content_tables) or set(content_after) != set(content_tables):
        raise RuntimeError("protected content digest table set does not match the manifest")

    rowcounts_ok = identities_ok = indexes_ok = True
    for table in protected:
        before_count = rowcounts_before.get(table)
        after_count = rowcounts_after.get(table)
        before_ids = identities_before.get(table)
        after_ids = identities_after.get(table)
        before_indexes = indexes_before.get(table)
        after_indexes = indexes_after.get(table)
        if (
            not isinstance(before_count, int)
            or isinstance(before_count, bool)
            or before_count < 1
            or not isinstance(after_count, int)
            or isinstance(after_count, bool)
            or after_count < 0
        ):
            raise RuntimeError(f"row counts are missing or invalid for {table}")
        if (
            not isinstance(before_ids, list)
            or not before_ids
            or len(before_ids) != len(set(before_ids))
            or any(not isinstance(identity, str) or not identity for identity in before_ids)
            or not isinstance(after_ids, list)
            or any(not isinstance(identity, str) or not identity for identity in after_ids)
        ):
            raise RuntimeError(f"identity samples are missing or invalid for {table}")
        if not isinstance(before_indexes, list) or not isinstance(after_indexes, list):
            raise RuntimeError(f"index snapshots are missing for {table}")
        rowcounts_ok = rowcounts_ok and after_count >= before_count
        identities_ok = identities_ok and after_ids == before_ids
        indexes_ok = indexes_ok and after_indexes == before_indexes

    content_ok = True
    for table in content_tables:
        before_fingerprint = _content_fingerprint(
            content_before.get(table), f"table_content_digests.before.{table}"
        )
        after_fingerprint = _content_fingerprint(
            content_after.get(table), f"table_content_digests.after.{table}"
        )
        content_ok = content_ok and after_fingerprint == before_fingerprint

    slots_before, slots_after = _phase_pair(document, "replication_slots")
    capacity_before, capacity_after = _phase_pair(document, "capacity")
    if not isinstance(slots_before, list) or not isinstance(slots_after, list):
        raise RuntimeError("replication_slots must be lists")
    capacity_before = _mapping(capacity_before, "capacity.before")
    capacity_after = _mapping(capacity_after, "capacity.after")
    return {
        "settings_before": settings_before,
        "settings_after": settings_after,
        "application_sessions_unblocked": sessions_unblocked,
        "protected_rowcounts_non_decreasing": rowcounts_ok,
        "protected_identities_survived": identities_ok,
        "protected_indexes_unchanged": indexes_ok,
        "protected_content_unchanged": content_ok,
        "replication_slots_unchanged": slots_after == slots_before,
        "capacity_unchanged": capacity_after == capacity_before,
    }


def evaluate(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    postgres = _postgres_checks(_json(run_dir / "sut" / "db_state.json"), manifest)
    postgres.pop("settings_before")
    postgres.pop("settings_after")
    timeout_scope = _timeout_scope_checks(
        _json(run_dir / "sut" / "task-protected-collector.json"),
        manifest,
    )

    docker_state = _json(run_dir / "docker_state.json")
    expected_services = {"svc-saleor-api", "db"}
    if set(docker_state) != expected_services:
        raise RuntimeError(
            "restart evidence service set mismatch: "
            f"expected={sorted(expected_services)} actual={sorted(docker_state)}"
        )
    zero_restart_repair = True
    restart_counts: dict[str, int] = {}
    for service in sorted(expected_services):
        state = docker_state[service]
        if not isinstance(state, dict) or state.get("running") is not True:
            raise RuntimeError(f"required service is not proven running: {service}")
        count = state.get("restart_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RuntimeError(f"restart count is malformed for {service}")
        restart_counts[service] = count
        zero_restart_repair = zero_restart_repair and count == 0

    semantics = {
        **timeout_scope,
        "zero_restart_repair": zero_restart_repair,
        **postgres,
    }
    return {
        "pass": all(semantics.values()),
        **semantics,
        "restart_counts": restart_counts,
    }
