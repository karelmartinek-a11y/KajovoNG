"""Execution authorization frozen from an explicit user Start action."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import canonical_sha256


@dataclass(frozen=True)
class ExecutionAuthorization:
    version: int
    approval_id: str
    run_id: str
    scope_hash: str
    model_bindings_hash: str
    policy_hash: str
    max_cost_microusd: int | None
    max_input_tokens: int
    max_output_tokens: int
    max_paid_requests: int
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
        "unknown_pricing": run_config["unknown_pricing"],
        "auto_repair": run_config["auto_repair"],
        "verification_profile_ids": run_config["verification_profile_ids"],
        "execution": run_config["execution"],
    }
    approval_seed = {
        "run_id": run_id,
        "scope_hash": scope_hash,
        "model_bindings_hash": canonical_sha256(bindings),
        "policy_hash": canonical_sha256(policy),
        "max_cost_microusd": run_config["max_cost_microusd"],
        "max_input_tokens": run_config["max_input_tokens"],
        "max_output_tokens": run_config["max_output_tokens"],
        "max_paid_requests": run_config["max_paid_requests"],
    }
    expires = datetime.now(timezone.utc) + timedelta(hours=lifetime_hours)
    return ExecutionAuthorization(
        version=1,
        approval_id="APPROVAL-" + canonical_sha256(approval_seed)[:32],
        run_id=run_id,
        scope_hash=scope_hash,
        model_bindings_hash=canonical_sha256(bindings),
        policy_hash=canonical_sha256(policy),
        max_cost_microusd=run_config["max_cost_microusd"],
        max_input_tokens=run_config["max_input_tokens"],
        max_output_tokens=run_config["max_output_tokens"],
        max_paid_requests=run_config["max_paid_requests"],
        network_allowlist=("api.openai.com",),
        expires_at=expires.isoformat(),
        repair_allowed=run_config["auto_repair"] == "within_approval",
    )
