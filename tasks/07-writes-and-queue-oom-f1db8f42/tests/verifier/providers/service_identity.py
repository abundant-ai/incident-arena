"""Pod/container identity proof, excluding one verifier-owned restart."""

from pathlib import Path
from typing import Any

from ..errors import EvidenceError
from ..materializers.common import read_json


def evaluate_service_identity(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    cfg = manifest["docker_state"]
    policy = cfg["restart_identity"]
    required = cfg["services"]
    protected = policy["protected_services"]
    challenge = policy["challenge_service"]
    if (
        not isinstance(required, list) or not required
        or not isinstance(protected, list) or not protected
        or any(not isinstance(s, str) or not s for s in required + protected)
        or len(set(required)) != len(required)
        or len(set(protected)) != len(protected)
        or not set(protected) <= set(required) or challenge not in protected
        or manifest.get("redis_state", {}).get("restart_challenge") is not True
    ):
        raise EvidenceError("invalid service restart identity policy")

    phases = {}
    for phase in ("baseline", "declaration", "soak_end"):
        snapshot = read_json(run_dir / "sut" / f"service_identity_{phase}.json", required=True)
        if not isinstance(snapshot, dict) or snapshot.get("error") or not isinstance(snapshot.get("components"), dict):
            raise EvidenceError(f"invalid service identity snapshot: {phase}")
        phases[phase] = snapshot["components"]
        for service in required:
            entry = phases[phase].get(service)
            # A successful API list with no such pod is real service failure.
            if entry is None:
                continue
            pods = entry.get("pods") if isinstance(entry, dict) else None
            if not isinstance(pods, dict) or not isinstance(entry.get("ready"), bool):
                raise EvidenceError(f"malformed pod identity: {phase}/{service}")
            for uid, containers in pods.items():
                if not isinstance(uid, str) or not uid or not isinstance(containers, dict):
                    raise EvidenceError(f"malformed pod identity: {phase}/{service}")
                for name, container in containers.items():
                    if (
                        not isinstance(name, str) or not name or not isinstance(container, dict)
                        or (
                            (not isinstance(container.get("container_id"), str) or not container["container_id"])
                            and (entry["ready"] or container.get("container_id") not in (None, ""))
                        )
                        or type(container.get("restart_count")) is not int or container["restart_count"] < 0
                    ):
                        raise EvidenceError(f"malformed container identity: {phase}/{service}")

    def pods(phase, service):
        return phases[phase].get(service, {}).get("pods", {})

    def populated(identity):
        return bool(identity) and all(identity.values())

    missing = [s for s in required if not populated(pods("soak_end", s)) or phases["soak_end"].get(s, {}).get("ready") is not True]
    violations = []
    for service in protected:
        before, declared, final = (pods(p, service) for p in phases)
        if not populated(before) or before != declared:
            violations.append(f"{service}: changed before the protected restart")
        if service != challenge and (not populated(final) or declared != final):
            violations.append(f"{service}: changed during recovery/soak")

    receipt = read_json(run_dir / "sut" / "queue_restart_receipt.json", required=True)
    if (
        not isinstance(receipt, dict) or set(receipt) != {"old_uid", "new_uid"}
        or any(not isinstance(v, str) or not v for v in receipt.values())
        or receipt["old_uid"] == receipt["new_uid"]
    ):
        raise EvidenceError("invalid protected queue restart receipt")
    declared, final = pods("declaration", challenge), pods("soak_end", challenge)
    if (
        set(declared) != {receipt["old_uid"]} or set(final) != {receipt["new_uid"]}
        or not populated(final)
        or any(c["restart_count"] != 0 for cs in final.values() for c in cs.values())
    ):
        violations.append(f"{challenge}: replacement does not match the protected restart")
    return {
        "pass": not missing and not violations,
        "all_running": not missing,
        "restart_legitimate": not violations,
        "missing_or_unready": missing,
        "restart_violations": violations,
    }
