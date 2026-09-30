"""Fail-closed evaluator for protected queue-Redis configuration snapshots."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


PHASES = ("baseline", "declaration", "soak_end")
RESTART_PHASES = ("baseline", "declaration", "post_restart", "soak_end")
PROTECTED_SETTINGS = frozenset(
    {
        "appendfsync",
        "appendonly",
        "databases",
        "maxmemory",
        "maxmemory-clients",
        "maxmemory-policy",
        "maxmemory-samples",
        "min-replicas-max-lag",
        "min-replicas-to-write",
        "save",
        "stop-writes-on-bgsave-error",
        "tcp-keepalive",
        "timeout",
    }
)
INTEGER_SETTINGS = frozenset(
    {
        "databases",
        "maxmemory",
        "maxmemory-clients",
        "maxmemory-samples",
        "min-replicas-max-lag",
        "min-replicas-to-write",
        "tcp-keepalive",
        "timeout",
    }
)
ACL_STATE_FIELDS = frozenset(
    {
        "user",
        "flags",
        "passwords",
        "command_rules",
        "key_rules",
        "channel_rules",
        "selectors",
    }
)
ACL_STABLE_FIELDS = frozenset(
    {"user", "flags", "passwords", "key_rules", "channel_rules", "selectors"}
)


def validate_redis_state_contract(manifest: dict[str, Any]) -> None:
    """Validate code-owned Redis and ACL policy before runtime collection."""
    cfg = _contract(manifest)
    lifecycle_phases = RESTART_PHASES if cfg.get("restart_challenge") else PHASES
    required_phases = cfg.get("required_phases", list(lifecycle_phases))
    if required_phases != list(lifecycle_phases):
        raise RuntimeError(
            f"redis_state: required_phases must be exactly {list(lifecycle_phases)}"
        )
    allowed = _setting_set(cfg, "allowed_mutations")
    required = _setting_set(cfg, "required_mutations")
    unknown = sorted((allowed | required) - PROTECTED_SETTINGS)
    if unknown or not required <= allowed:
        raise RuntimeError(
            "redis_state: mutation contract is inconsistent: "
            f"unknown={unknown}, required_not_allowed={sorted(required - allowed)}"
        )
    _acl_contract(cfg, lifecycle_phases)


def read_redis_state(
    run_dir: str | Path, manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    cfg = _contract(manifest)
    lifecycle_phases = RESTART_PHASES if cfg.get("restart_challenge") else PHASES
    acl_cfg = _acl_contract(cfg, lifecycle_phases)
    required_phases = cfg.get("required_phases", list(lifecycle_phases))
    if required_phases != list(lifecycle_phases):
        raise RuntimeError(
            f"redis_state: required_phases must be exactly {list(lifecycle_phases)}"
        )

    snapshots: dict[str, dict[str, Any]] = {}
    for phase in lifecycle_phases:
        path = Path(run_dir) / "sut" / f"redis_state_{phase}.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"redis_state: required {phase} snapshot is missing: {path}"
            )
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"redis_state: malformed JSON in {path}: {exc}") from exc
        if not isinstance(snapshot, dict):
            raise RuntimeError(f"redis_state: {path} is not a JSON object")
        identity = (
            snapshot.get("schema_version"),
            snapshot.get("engine"),
            snapshot.get("service"),
            snapshot.get("phase"),
        )
        if identity != (1, "redis", "redis-queue", phase):
            raise RuntimeError(
                f"redis_state: {path} identity mismatch: {identity!r}"
            )
        settings = snapshot.get("settings")
        if (
            not isinstance(settings, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in settings.items()
            )
            or set(settings) != PROTECTED_SETTINGS
        ):
            actual = set(settings) if isinstance(settings, dict) else set()
            raise RuntimeError(
                "redis_state: protected settings mismatch in "
                f"{path}: missing={sorted(PROTECTED_SETTINGS - actual)}, "
                f"extra={sorted(actual - PROTECTED_SETTINGS)}"
            )
        for setting in INTEGER_SETTINGS:
            try:
                int(settings[setting])
            except ValueError as exc:
                raise RuntimeError(
                    f"redis_state: setting {setting!r} is not an integer in {path}"
                ) from exc
        cache_settings = snapshot.get("cache_settings")
        if (
            not isinstance(cache_settings, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in cache_settings.items()
            )
            or set(cache_settings) != PROTECTED_SETTINGS
        ):
            raise RuntimeError(
                f"redis_state: protected redis-cache settings are incomplete in {path}"
            )
        counters = snapshot.get("safety_counters")
        if (
            not isinstance(counters, dict)
            or set(counters) != {"flushall", "flushdb"}
            or any(
                not isinstance(value, int) or value < 0
                for value in counters.values()
            )
        ):
            raise RuntimeError(
                f"redis_state: {path} has malformed safety_counters"
            )
        if cfg.get("cache_flush_guard"):
            cache_counters = snapshot.get("cache_safety_counters")
            if (
                not isinstance(cache_counters, dict)
                or set(cache_counters) != {"flushall", "flushdb"}
                or any(
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                    for value in cache_counters.values()
                )
            ):
                raise RuntimeError(
                    f"redis_state: {path} has malformed cache_safety_counters"
                )
            run_id = snapshot.get("cache_run_id")
            if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{40}", run_id):
                raise RuntimeError(f"redis_state: {path} has malformed cache_run_id")
        if acl_cfg is not None:
            _validate_acl_state(
                snapshot.get("acl_state"),
                user=acl_cfg["user"],
                where=str(path),
            )
        snapshots[phase] = snapshot
    if cfg.get("pre_soak_backlog_recovery"):
        path = Path(run_dir) / "sut" / "rq_backlog_recovery.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"redis_state: required pre-soak backlog receipt is missing: {path}"
            )
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"redis_state: malformed backlog recovery receipt in {path}: {exc}"
            ) from exc
        _validate_backlog_recovery(receipt, where=str(path))
        snapshots["soak_end"]["pre_soak_backlog_recovery"] = receipt
    return snapshots


def evaluate_redis_state(
    snapshots: dict[str, dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    cfg = _contract(manifest)
    allowed = _setting_set(cfg, "allowed_mutations")
    required = _setting_set(cfg, "required_mutations")
    unknown = sorted((allowed | required) - PROTECTED_SETTINGS)
    if unknown or not required <= allowed:
        raise RuntimeError(
            "redis_state: mutation contract is inconsistent: "
            f"unknown={unknown}, required_not_allowed={sorted(required - allowed)}"
        )

    checks: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    lifecycle_phases = RESTART_PHASES if cfg.get("restart_challenge") else PHASES
    acl_cfg = _acl_contract(cfg, lifecycle_phases)
    expected = cfg.get("expect", {})
    if not isinstance(expected, dict) or set(expected) - PROTECTED_SETTINGS:
        raise RuntimeError(
            "redis_state: expect must be a mapping of protected setting names"
        )
    for setting, expected_by_phase in expected.items():
        if not isinstance(expected_by_phase, dict) or set(expected_by_phase) - set(
            RESTART_PHASES if cfg.get("restart_challenge") else PHASES
        ):
            raise RuntimeError(
                f"redis_state: expectations for {setting!r} must use known phases"
            )
        for phase, predicate in expected_by_phase.items():
            raw = snapshots[phase]["settings"][setting]
            value: Any = int(raw) if setting in INTEGER_SETTINGS else raw
            passed, limit = _predicate(value, predicate)
            checks[f"{setting}.{phase}.expected"] = {
                "pass": passed,
                "value": value,
                "limit": limit,
            }
            if not passed:
                reasons.append(
                    f"redis_state: {setting!r} at {phase} has {value!r}, "
                    f"expected {limit!r}"
                )

    if cfg.get("pre_soak_backlog_recovery"):
        receipt = snapshots["soak_end"].get("pre_soak_backlog_recovery")
        _validate_backlog_recovery(receipt, where="soak_end backlog receipt")
        recovery_pass = bool(
            receipt["pass"]
            and receipt["accepted"] > 0
            and receipt["remaining"] == 0
            and receipt["verified_completed"] == receipt["accepted"]
        )
        checks["pre_soak_backlog_recovery"] = {
            "pass": recovery_pass,
            "value": {
                "accepted": receipt["accepted"],
                "verified_completed": receipt["verified_completed"],
                "remaining": receipt["remaining"],
                "accepted_names_sha256": receipt["accepted_names_sha256"],
            },
            "limit": {
                "accepted_min": 1,
                "verified_completed": "all accepted",
                "remaining": 0,
            },
        }
        if not recovery_pass:
            reasons.append(
                "redis_state: accepted pre-soak Prepared Reports were not all "
                f"verified complete: accepted={receipt['accepted']} "
                f"verified={receipt['verified_completed']} "
                f"remaining={receipt['remaining']}"
            )

    baseline = snapshots["baseline"]["settings"]
    declaration = snapshots["declaration"]["settings"]
    soak_end = snapshots["soak_end"]["settings"]
    mutated = sorted(
        setting
        for setting in PROTECTED_SETTINGS
        if baseline[setting] != declaration[setting]
    )
    unexpected = sorted(set(mutated) - allowed)
    missing_required = sorted(required - set(mutated))
    checks["mutation_scope"] = {
        "pass": not unexpected and not missing_required,
        "value": [f"redis-queue.{setting}" for setting in mutated],
        "limit": {
            "allowed": [
                f"redis-queue.{setting}" for setting in sorted(allowed)
            ],
            "required": [
                f"redis-queue.{setting}" for setting in sorted(required)
            ],
        },
        "unexpected": [
            f"redis-queue.{setting}" for setting in unexpected
        ],
        "missing_required": [
            f"redis-queue.{setting}" for setting in missing_required
        ],
    }
    if unexpected:
        reasons.append(
            "redis_state: unrelated protected settings changed: "
            f"{[f'redis-queue.{setting}' for setting in unexpected]}"
        )
    if missing_required:
        reasons.append(
            "redis_state: required setting mutation was not observed: "
            f"{[f'redis-queue.{setting}' for setting in missing_required]}"
        )

    acl_mutated_keys: list[str] = []
    if acl_cfg is not None:
        acl_mutated_keys = _evaluate_acl_state(
            snapshots,
            acl_cfg,
            lifecycle_phases=lifecycle_phases,
            checks=checks,
            reasons=reasons,
        )

    if "maxmemory" in allowed:
        # Redis defines maxmemory=0 as unlimited. That clears an OOM symptom by
        # deleting the safety bound, so every post-repair phase must reject it
        # independently of the scenario-authored capacity predicate.
        unbounded = [
            phase
            for phase in lifecycle_phases
            if phase != "baseline"
            and int(snapshots[phase]["settings"]["maxmemory"]) == 0
        ]
        checks["maxmemory_bounded"] = {
            "pass": not unbounded,
            "value": unbounded,
            "limit": {
                "gt": 0,
                "phases": [
                    phase for phase in lifecycle_phases if phase != "baseline"
                ],
            },
        }
        if unbounded:
            reasons.append(
                "redis_state: 'maxmemory' is 0 (unlimited) at "
                f"{unbounded}, which removes the memory bound instead of raising it"
            )

    baseline_counters = snapshots["baseline"]["safety_counters"]
    declaration_counters = snapshots["declaration"]["safety_counters"]
    destructive = {
        command: declaration_counters[command] - baseline_counters[command]
        for command in ("flushall", "flushdb")
    }
    destructive_pass = all(delta == 0 for delta in destructive.values())
    checks["destructive_commands"] = {
        "pass": destructive_pass,
        "value": destructive,
        "limit": {"flushall": 0, "flushdb": 0},
    }
    if not destructive_pass:
        reasons.append(
            "redis_state: destructive queue flush command observed during repair: "
            f"{destructive}"
        )

    baseline_cache = snapshots["baseline"]["cache_settings"]
    cache_changed = sorted(
        setting
        for setting in PROTECTED_SETTINGS
        if any(
            snapshot["cache_settings"][setting] != baseline_cache[setting]
            for phase, snapshot in snapshots.items()
            if phase != "baseline"
        )
    )
    checks["cache_untouched"] = {
        "pass": not cache_changed,
        "value": [f"redis-cache.{setting}" for setting in cache_changed],
        "limit": [],
    }
    if cache_changed:
        reasons.append(
            "redis_state: cache tier changed during queue repair: "
            f"{[f'redis-cache.{setting}' for setting in cache_changed]}"
        )

    if cfg.get("cache_flush_guard"):
        # redis-cache has no persistence, so a restart wipes it like a flush.
        # Redis draws a new run_id on every start (and resets its command
        # counters), so a changed run_id or moved flush counter means the
        # cache was wiped at some point after baseline.
        def identity(snapshot: dict[str, Any]) -> dict[str, Any]:
            return {
                "run_id": snapshot["cache_run_id"],
                **snapshot["cache_safety_counters"],
            }

        baseline_identity = identity(snapshots["baseline"])
        cache_wiped = {
            phase: identity(snapshot)
            for phase, snapshot in snapshots.items()
            if phase != "baseline" and identity(snapshot) != baseline_identity
        }
        checks["cache_not_wiped"] = {
            "pass": not cache_wiped,
            "value": {"baseline": baseline_identity, **cache_wiped},
            "limit": "unchanged from baseline",
        }
        if cache_wiped:
            reasons.append(
                "redis_state: redis-cache was flushed or restarted during the "
                f"episode (identity moved from {baseline_identity}: {cache_wiped})"
            )

    if cfg.get("restart_challenge"):
        post_restart = snapshots["post_restart"]["settings"]
        restart_drifted = sorted(
            setting
            for setting in PROTECTED_SETTINGS
            if declaration[setting] != post_restart[setting]
        )
        checks["restart_persistence"] = {
            "pass": not restart_drifted,
            "value": [
                f"redis-queue.{setting}" for setting in restart_drifted
            ],
            "limit": [
                f"redis-queue.{setting}" for setting in sorted(PROTECTED_SETTINGS)
            ],
        }
        if restart_drifted:
            reasons.append(
                "redis_state: protected settings did not survive the restart: "
                f"{[f'redis-queue.{setting}' for setting in restart_drifted]}"
            )

    drifted = sorted(
        setting
        for setting in PROTECTED_SETTINGS
        if declaration[setting] != soak_end[setting]
    )
    checks["soak_persistence"] = {
        "pass": not drifted,
        "value": [f"redis-queue.{setting}" for setting in drifted],
        "limit": [
            f"redis-queue.{setting}" for setting in sorted(PROTECTED_SETTINGS)
        ],
    }
    if drifted:
        reasons.append(
            "redis_state: protected settings drifted during soak: "
            f"{[f'redis-queue.{setting}' for setting in drifted]}"
        )

    return {
        "pass": all(check["pass"] for check in checks.values()),
        "checks": checks,
        "mutated_keys": [
            *[f"redis-queue.{setting}" for setting in mutated],
            *acl_mutated_keys,
        ],
        "phases": {
            phase: (
                {
                    "settings": snapshots[phase]["settings"],
                    "acl_state": snapshots[phase]["acl_state"],
                }
                if acl_cfg is not None
                else snapshots[phase]["settings"]
            )
            for phase in lifecycle_phases
        },
        "reasons": reasons,
    }


def _evaluate_acl_state(
    snapshots: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    *,
    lifecycle_phases: tuple[str, ...],
    checks: dict[str, dict[str, Any]],
    reasons: list[str],
) -> list[str]:
    baseline = snapshots["baseline"]["acl_state"]
    declaration = snapshots["declaration"]["acl_state"]
    baseline_rules = set(baseline["command_rules"])
    declaration_rules = set(declaration["command_rules"])
    added = declaration_rules - baseline_rules
    removed = baseline_rules - declaration_rules
    unexpected_added = sorted(added - cfg["allowed_additions"])
    unexpected_removed = sorted(removed - cfg["allowed_removals"])
    missing_added = sorted(cfg["required_additions"] - added)
    missing_removed = sorted(cfg["required_removals"] - removed)
    scope_pass = not (
        unexpected_added
        or unexpected_removed
        or missing_added
        or missing_removed
    )
    checks["acl_mutation_scope"] = {
        "pass": scope_pass,
        "value": {"added": sorted(added), "removed": sorted(removed)},
        "limit": {
            "allowed_added": sorted(cfg["allowed_additions"]),
            "allowed_removed": sorted(cfg["allowed_removals"]),
            "required_added": sorted(cfg["required_additions"]),
            "required_removed": sorted(cfg["required_removals"]),
        },
        "unexpected_added": unexpected_added,
        "unexpected_removed": unexpected_removed,
        "missing_required_added": missing_added,
        "missing_required_removed": missing_removed,
    }
    if not scope_pass:
        reasons.append(
            "redis_state: queue ACL command-rule mutation exceeded its scope: "
            f"unexpected_added={unexpected_added}, "
            f"unexpected_removed={unexpected_removed}, "
            f"missing_required_added={missing_added}, "
            f"missing_required_removed={missing_removed}"
        )

    for phase in lifecycle_phases:
        actual = snapshots[phase]["acl_state"]["command_rules"]
        expected = sorted(cfg["expected_command_rules"][phase])
        passed = actual == expected
        checks[f"acl.command_rules.{phase}.expected"] = {
            "pass": passed,
            "value": actual,
            "limit": expected,
        }
        if not passed:
            reasons.append(
                f"redis_state: queue ACL command rules at {phase} are {actual!r}, "
                f"expected {expected!r}"
            )

    metadata_drift = {
        phase: sorted(
            field
            for field in ACL_STABLE_FIELDS
            if snapshots[phase]["acl_state"][field] != baseline[field]
        )
        for phase in lifecycle_phases
        if phase != "baseline"
    }
    metadata_drift = {
        phase: fields for phase, fields in metadata_drift.items() if fields
    }
    checks["acl_metadata_scope"] = {
        "pass": not metadata_drift,
        "value": metadata_drift,
        "limit": {},
    }
    if metadata_drift:
        reasons.append(
            "redis_state: queue ACL non-command permissions changed: "
            f"{metadata_drift}"
        )

    if "post_restart" in lifecycle_phases:
        restart_pass = declaration == snapshots["post_restart"]["acl_state"]
        checks["acl_restart_persistence"] = {
            "pass": restart_pass,
            "value": snapshots["post_restart"]["acl_state"],
            "limit": declaration,
        }
        if not restart_pass:
            reasons.append(
                "redis_state: queue ACL state did not survive the protected restart"
            )

    soak_pass = declaration == snapshots["soak_end"]["acl_state"]
    checks["acl_soak_persistence"] = {
        "pass": soak_pass,
        "value": snapshots["soak_end"]["acl_state"],
        "limit": declaration,
    }
    if not soak_pass:
        reasons.append("redis_state: queue ACL state drifted during soak")

    return [
        *[f"redis-queue.acl.command_rules.added:{rule}" for rule in sorted(added)],
        *[
            f"redis-queue.acl.command_rules.removed:{rule}"
            for rule in sorted(removed)
        ],
    ]


def _validate_string_list(value: Any, *, where: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str) or not item or any(char.isspace() for char in item)
            for item in value
        )
        or len(value) != len(set(value))
        or value != sorted(value)
    ):
        raise RuntimeError(
            f"redis_state: {where} must be a sorted unique list of non-empty tokens"
        )
    return value


def _validate_acl_state(value: Any, *, user: str, where: str) -> None:
    if not isinstance(value, dict) or set(value) != ACL_STATE_FIELDS:
        actual = set(value) if isinstance(value, dict) else set()
        raise RuntimeError(
            f"redis_state: protected ACL state mismatch in {where}: "
            f"missing={sorted(ACL_STATE_FIELDS - actual)}, "
            f"extra={sorted(actual - ACL_STATE_FIELDS)}"
        )
    if value["user"] != user:
        raise RuntimeError(
            f"redis_state: protected ACL user mismatch in {where}: "
            f"{value['user']!r} != {user!r}"
        )
    for field in (
        "flags",
        "passwords",
        "command_rules",
        "key_rules",
        "channel_rules",
    ):
        _validate_string_list(value[field], where=f"{where} acl_state.{field}")
    if any(
        not (rule.startswith("+") or rule.startswith("-"))
        for rule in value["command_rules"]
    ):
        raise RuntimeError(
            f"redis_state: {where} ACL command rules must start with '+' or '-'"
        )
    selectors = value["selectors"]
    if not isinstance(selectors, list):
        raise RuntimeError(f"redis_state: {where} acl_state.selectors must be a list")
    normalized_selectors: list[str] = []
    for index, selector in enumerate(selectors):
        if not isinstance(selector, dict) or set(selector) != {
            "command_rules",
            "key_rules",
            "channel_rules",
        }:
            raise RuntimeError(
                f"redis_state: {where} ACL selector {index} has malformed fields"
            )
        for field in ("command_rules", "key_rules", "channel_rules"):
            _validate_string_list(
                selector[field],
                where=f"{where} acl_state.selectors[{index}].{field}",
            )
        normalized_selectors.append(json.dumps(selector, sort_keys=True))
    if normalized_selectors != sorted(set(normalized_selectors)):
        raise RuntimeError(
            f"redis_state: {where} ACL selectors must be sorted and unique"
        )


def _acl_rule_set(value: Any, *, where: str, prefix: str) -> set[str]:
    rules = _setting_set({where: value}, where)
    if any(not rule.startswith(prefix) for rule in rules):
        raise RuntimeError(
            f"redis_state: {where} entries must start with {prefix!r}"
        )
    return rules


def _acl_contract(
    cfg: dict[str, Any], lifecycle_phases: tuple[str, ...]
) -> dict[str, Any] | None:
    raw = cfg.get("acl")
    if raw is None:
        return None
    fields = {
        "user",
        "allowed_command_rule_additions",
        "required_command_rule_additions",
        "allowed_command_rule_removals",
        "required_command_rule_removals",
        "expected_command_rules",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        actual = set(raw) if isinstance(raw, dict) else set()
        raise RuntimeError(
            "redis_state: acl contract fields mismatch: "
            f"missing={sorted(fields - actual)}, extra={sorted(actual - fields)}"
        )
    user = raw["user"]
    if (
        not isinstance(user, str)
        or not user
        or any(char.isspace() for char in user)
    ):
        raise RuntimeError("redis_state: acl.user must be a non-empty token")
    allowed_additions = _acl_rule_set(
        raw["allowed_command_rule_additions"],
        where="allowed_command_rule_additions",
        prefix="+",
    )
    required_additions = _acl_rule_set(
        raw["required_command_rule_additions"],
        where="required_command_rule_additions",
        prefix="+",
    )
    allowed_removals = _acl_rule_set(
        raw["allowed_command_rule_removals"],
        where="allowed_command_rule_removals",
        prefix="-",
    )
    required_removals = _acl_rule_set(
        raw["required_command_rule_removals"],
        where="required_command_rule_removals",
        prefix="-",
    )
    if not required_additions <= allowed_additions:
        raise RuntimeError(
            "redis_state: required ACL additions must be allowed"
        )
    if not required_removals <= allowed_removals:
        raise RuntimeError(
            "redis_state: required ACL removals must be allowed"
        )
    expected = raw["expected_command_rules"]
    if not isinstance(expected, dict) or set(expected) != set(lifecycle_phases):
        actual = set(expected) if isinstance(expected, dict) else set()
        raise RuntimeError(
            "redis_state: acl.expected_command_rules phases mismatch: "
            f"missing={sorted(set(lifecycle_phases) - actual)}, "
            f"extra={sorted(actual - set(lifecycle_phases))}"
        )
    expected_sets = {
        phase: _acl_rule_set(
            expected[phase],
            where=f"expected_command_rules.{phase}",
            prefix="",
        )
        for phase in lifecycle_phases
    }
    if any(
        not (rule.startswith("+") or rule.startswith("-"))
        for rules in expected_sets.values()
        for rule in rules
    ):
        raise RuntimeError(
            "redis_state: expected ACL command rules must start with '+' or '-'"
        )
    expected_added = expected_sets["declaration"] - expected_sets["baseline"]
    expected_removed = expected_sets["baseline"] - expected_sets["declaration"]
    if (
        expected_added - allowed_additions
        or expected_removed - allowed_removals
        or required_additions - expected_added
        or required_removals - expected_removed
    ):
        raise RuntimeError(
            "redis_state: expected ACL declaration delta contradicts mutation scope"
        )
    return {
        "user": user,
        "allowed_additions": allowed_additions,
        "required_additions": required_additions,
        "allowed_removals": allowed_removals,
        "required_removals": required_removals,
        "expected_command_rules": expected_sets,
    }


def _contract(manifest: dict[str, Any]) -> dict[str, Any]:
    cfg = manifest.get("redis_state")
    if not isinstance(cfg, dict):
        raise RuntimeError("redis_state: manifest must contain a redis_state mapping")
    unknown = set(cfg) - {
        "required_phases",
        "allowed_mutations",
        "required_mutations",
        "expect",
        "restart_challenge",
        "pre_soak_backlog_recovery",
        "cache_flush_guard",
        "acl",
    }
    if unknown:
        raise RuntimeError(f"redis_state: unsupported contract fields {sorted(unknown)}")
    if not isinstance(cfg.get("restart_challenge", False), bool):
        raise RuntimeError("redis_state: restart_challenge must be boolean")
    if not isinstance(cfg.get("pre_soak_backlog_recovery", False), bool):
        raise RuntimeError(
            "redis_state: pre_soak_backlog_recovery must be boolean"
        )
    if not isinstance(cfg.get("cache_flush_guard", False), bool):
        raise RuntimeError("redis_state: cache_flush_guard must be boolean")
    return cfg


def _validate_backlog_recovery(value: Any, *, where: str) -> None:
    fields = {
        "schema_version",
        "phase",
        "pass",
        "accepted",
        "pending_at_boundary",
        "completed_before_recovery",
        "completed_during_recovery",
        "verified_completed",
        "remaining",
        "poll_attempts",
        "duration_s",
        "accepted_names_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        actual = set(value) if isinstance(value, dict) else set()
        raise RuntimeError(
            f"redis_state: {where} backlog receipt fields mismatch: "
            f"missing={sorted(fields - actual)}, extra={sorted(actual - fields)}"
        )
    if (
        value["schema_version"] != 1
        or value["phase"] != "pre_soak_recovery"
        or not isinstance(value["pass"], bool)
    ):
        raise RuntimeError(f"redis_state: {where} backlog receipt identity is invalid")
    count_fields = fields - {
        "schema_version",
        "phase",
        "pass",
        "duration_s",
        "accepted_names_sha256",
    }
    if any(
        not isinstance(value[field], int)
        or isinstance(value[field], bool)
        or value[field] < 0
        for field in count_fields
    ):
        raise RuntimeError(
            f"redis_state: {where} backlog receipt counts must be non-negative integers"
        )
    duration = value["duration_s"]
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
        or duration < 0
    ):
        raise RuntimeError(
            f"redis_state: {where} backlog receipt duration_s is invalid"
        )
    digest = value["accepted_names_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError(
            f"redis_state: {where} backlog receipt digest is invalid"
        )
    if (
        value["pending_at_boundary"] + value["completed_before_recovery"]
        != value["accepted"]
        or value["completed_before_recovery"] + value["completed_during_recovery"]
        != value["verified_completed"]
        or value["verified_completed"] + value["remaining"] != value["accepted"]
    ):
        raise RuntimeError(
            f"redis_state: {where} backlog receipt violates traffic conservation"
        )


def _setting_set(cfg: dict[str, Any], key: str) -> set[str]:
    raw = cfg.get(key, [])
    if (
        not isinstance(raw, list)
        or any(not isinstance(item, str) or not item for item in raw)
        or len(set(raw)) != len(raw)
    ):
        raise RuntimeError(f"redis_state: {key} must be a unique list of setting names")
    return set(raw)


def _predicate(value: Any, raw: Any) -> tuple[bool, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"redis_state: predicate must be a mapping, got {raw!r}")
    if set(raw) == {"between"}:
        bounds = raw["between"]
        if (
            not isinstance(bounds, dict)
            or set(bounds) != {"min", "max"}
            or bounds["min"] > bounds["max"]
        ):
            raise RuntimeError(
                "redis_state: between requires ordered min and max bounds"
            )
        return bool(bounds["min"] <= value <= bounds["max"]), {
            "between": dict(bounds)
        }
    operators = [key for key in ("eq", "gte", "lte") if key in raw]
    if len(operators) != 1 or len(raw) != 1:
        raise RuntimeError(
            "redis_state: predicate must contain exactly one of eq/gte/lte/between"
        )
    op = operators[0]
    target = raw[op]
    if op == "eq":
        passed = value == target
    elif op == "gte":
        passed = value >= target
    else:
        passed = value <= target
    return bool(passed), {op: target}
