"""Immutable WorkOrder V2 used as the trusted identity of paid work."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict, dataclass
from typing import Any

import jsonschema

from .contracts import canonical_bytes, canonical_sha256

WORK_ORDER_V2_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "version": {"type": "integer", "enum": [2]},
        "run_id": {"type": "string"},
        "step_id": {"type": "string"},
        "task_id": {"type": "string"},
        "stage": {"type": "string"},
        "route": {
            "type": "string",
            "enum": ["local", "responses_live", "responses_batch", "image_live", "image_batch"],
        },
        "target_id": {"type": "string"},
        "target_path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "expected_target_hash": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "input_projection_hash": {"type": "string"},
        "contract_name": {"type": "string"},
        "schema_hash": {"type": "string"},
        "prompt_hash": {"type": "string"},
        "model": {"type": "string"},
        "model_capability_hash": {"type": "string"},
        "policy_hash": {"type": "string"},
        "source_snapshot_hash": {"type": "string"},
        "budget_reservation_id": {"type": "string"},
        "approval_id": {"type": "string"},
        "attempt_no": {"type": "integer"},
    },
    "required": [
        "version", "run_id", "step_id", "task_id", "stage", "route", "target_id",
        "target_path", "expected_target_hash", "input_projection_hash", "contract_name",
        "schema_hash", "prompt_hash", "model", "model_capability_hash", "policy_hash",
        "source_snapshot_hash", "budget_reservation_id", "approval_id", "attempt_no",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class WorkOrder:
    version: int
    run_id: str
    step_id: str
    task_id: str
    stage: str
    route: str
    target_id: str
    target_path: str | None
    expected_target_hash: str | None
    input_projection_hash: str
    contract_name: str
    schema_hash: str
    prompt_hash: str
    model: str
    model_capability_hash: str
    policy_hash: str
    source_snapshot_hash: str
    budget_reservation_id: str
    approval_id: str
    attempt_no: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        validate_work_order_v2(value)
        return value

    @property
    def order_hash(self) -> str:
        return canonical_sha256(self.to_dict())


def validate_work_order_v2(value: dict[str, Any]) -> None:
    try:
        jsonschema.Draft202012Validator(WORK_ORDER_V2_SCHEMA).validate(value)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"WORK_ORDER_V2: {exc.message}") from exc
    if value["attempt_no"] < 0:
        raise ValueError("WORK_ORDER_V2.attempt_no nesmí být záporné.")
    for key in (
        "run_id", "step_id", "task_id", "stage", "target_id",
        "input_projection_hash", "contract_name", "schema_hash", "prompt_hash",
        "model", "model_capability_hash", "policy_hash", "source_snapshot_hash",
        "budget_reservation_id", "approval_id",
    ):
        if not value[key]:
            raise ValueError(f"WORK_ORDER_V2.{key} nesmí být prázdné.")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def freeze_order(config: Any, task: dict[str, Any], projection: Any) -> WorkOrder:
    """Freeze a canonical V2 order before transport.

    The target identity/path/action comes only from trusted local task data.
    Provider responses can never mutate these fields.
    """
    stage = str(task["stage"])
    target_path = task.get("target_path")
    target_id = str(task.get("target_id") or target_path or stage)
    schema = copy.deepcopy(task["schema"])
    prompt = str(task["prompt"])
    model = str(task["model"])
    route = str(task.get("route") or "responses_live")
    attempt_no = int(task.get("attempt_no", 0))
    approval_id = str(task.get("approval_id") or f"user-start:{task['run_id']}")
    source_snapshot = task.get("source_snapshot") or {}
    capability = task.get("model_capability") or {}
    policy = {
        "quality": getattr(config, "maximum_quality", False),
        "auto_repair": getattr(config, "auto_repair", "off"),
        "unknown_pricing": getattr(config, "unknown_pricing", "block"),
        "max_cost_microusd": getattr(config, "max_cost_microusd", None),
        "max_input_tokens": getattr(config, "max_input_tokens", None),
        "max_output_tokens": getattr(config, "max_output_tokens", None),
        "max_paid_requests": getattr(config, "max_paid_requests", None),
    }
    reservation_seed = {
        "run_id": task["run_id"],
        "stage": stage,
        "target_id": target_id,
        "projection_hash": _hash(projection),
        "schema_hash": _hash(schema),
        "prompt_hash": _hash(prompt),
        "model": model,
        "attempt_no": attempt_no,
    }
    order = WorkOrder(
        version=2,
        run_id=str(task["run_id"]),
        step_id=str(task["step_id"]),
        task_id=str(task.get("task_id") or ("TASK-" + _hash(reservation_seed)[:24])),
        stage=stage,
        route=route,
        target_id=target_id,
        target_path=str(target_path) if target_path is not None else None,
        expected_target_hash=(
            str(task["expected_target_hash"])
            if task.get("expected_target_hash") is not None else None
        ),
        input_projection_hash=_hash(projection),
        contract_name=str(task["contract_name"]),
        schema_hash=_hash(schema),
        prompt_hash=_hash(prompt),
        model=model,
        model_capability_hash=_hash(capability),
        policy_hash=_hash(policy),
        source_snapshot_hash=_hash(source_snapshot),
        budget_reservation_id="RES-" + _hash(reservation_seed)[:32],
        approval_id=approval_id,
        attempt_no=attempt_no,
    )
    validate_work_order_v2(order.to_dict())
    return order
