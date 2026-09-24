"""Execution authorization frozen from an explicit user Start action."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import canonical_sha256


def automatic_attempt_limit(config) -> int:
    """První pokus je povolen startem; další jen explicitní politikou oprav."""
    policy = config.get("auto_repair") if isinstance(config, dict) else getattr(config, "auto_repair", "off")
    return 3 if policy == "within_approval" else 1


@dataclass(frozen=True)
class ExecutionAuthorization:
    version: int
    approval_id: str
    run_id: str
    scope_hash: str
    model_bindings_hash: str
    policy_hash: str
    network_allowlist: tuple[str, ...]
    expires_at: str
    repair_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["network_allowlist"] = list(self.network_allowlist)
        return value


def create_execution_authorization(
    run_id: str,
    run_config: dict[str, Any],
    scope_hash: str,
    *,
    lifetime_hours: int = 12,
) -> ExecutionAuthorization:
    bindings = run_config["model_bindings"]
    policy = {
        "quality": run_config["quality"],
        "auto_repair": run_config["auto_repair"],
        "verification_profile_ids": run_config["verification_profile_ids"],
        "execution": run_config["execution"],
        "stop_after_plan": run_config["stop_after_plan"],
        "dry_run": run_config["dry_run"],
    }
    approval_seed = {
        "run_id": run_id,
        "scope_hash": scope_hash,
        "model_bindings_hash": canonical_sha256(bindings),
        "policy_hash": canonical_sha256(policy),
    }
    expires = datetime.now(timezone.utc) + timedelta(hours=lifetime_hours)
    return ExecutionAuthorization(
        version=1,
        approval_id="APPROVAL-" + canonical_sha256(approval_seed)[:32],
        run_id=run_id,
        scope_hash=scope_hash,
        model_bindings_hash=canonical_sha256(bindings),
        policy_hash=canonical_sha256(policy),
        network_allowlist=("api.openai.com",),
        expires_at=expires.isoformat(),
        repair_allowed=run_config["auto_repair"] == "within_approval",
    )



@dataclass(frozen=True)
class TargetedExecutionAuthorization:
    version: int
    approval_id: str
    run_id: str
    scope_hash: str
    purpose: str
    target_paths: tuple[str, ...]
    source_manifest_hash: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["target_paths"] = list(self.target_paths)
        return value


def validate_execution_authorization(
    authorization: dict[str, Any],
    *,
    run_id: str,
    run_config: dict[str, Any],
    scope_hash: str,
    require_repair: bool = False,
    allow_expired: bool = False,
) -> ExecutionAuthorization:
    """Validate the frozen root Start authorization without extending its scope."""
    if not isinstance(authorization, dict):
        raise ValueError("execution_authorization missing")
    expected = create_execution_authorization(
        run_id,
        run_config,
        scope_hash,
    )
    stable = expected.to_dict()
    for key in (
        "version",
        "approval_id",
        "run_id",
        "scope_hash",
        "model_bindings_hash",
        "policy_hash",
        "network_allowlist",
        "repair_allowed",
    ):
        if authorization.get(key) != stable[key]:
            raise ValueError(f"execution_authorization mismatch: {key}")
    try:
        expires = datetime.fromisoformat(str(authorization["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("execution_authorization expiry invalid") from exc
    if expires.tzinfo is None:
        raise ValueError("execution_authorization expiry must be timezone-aware")
    if not allow_expired and expires <= datetime.now(timezone.utc):
        raise ValueError("execution_authorization expired")
    if require_repair and authorization.get("repair_allowed") is not True:
        raise ValueError("execution_authorization does not allow repair")
    return ExecutionAuthorization(
        version=int(authorization["version"]),
        approval_id=str(authorization["approval_id"]),
        run_id=str(authorization["run_id"]),
        scope_hash=str(authorization["scope_hash"]),
        model_bindings_hash=str(authorization["model_bindings_hash"]),
        policy_hash=str(authorization["policy_hash"]),
        network_allowlist=tuple(authorization["network_allowlist"]),
        expires_at=str(authorization["expires_at"]),
        repair_allowed=bool(authorization["repair_allowed"]),
    )


def create_targeted_retry_authorization(
    run_id: str,
    scope_hash: str,
    target_paths: list[str] | tuple[str, ...] | set[str],
    source_manifest_hash: str,
    *,
    lifetime_hours: int = 2,
) -> TargetedExecutionAuthorization:
    targets = tuple(sorted({str(path) for path in target_paths if str(path)}))
    seed = {
        "run_id": run_id,
        "scope_hash": scope_hash,
        "purpose": "manual_retry",
        "target_paths": list(targets),
        "source_manifest_hash": source_manifest_hash,
    }
    expires = datetime.now(timezone.utc) + timedelta(hours=lifetime_hours)
    return TargetedExecutionAuthorization(
        version=1,
        approval_id="RETRY-" + canonical_sha256(seed)[:32],
        run_id=run_id,
        scope_hash=scope_hash,
        purpose="manual_retry",
        target_paths=targets,
        source_manifest_hash=source_manifest_hash,
        expires_at=expires.isoformat(),
    )
