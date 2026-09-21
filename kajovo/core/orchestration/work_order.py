"""Immutable WorkOrder V2 used as the trusted identity of provider work."""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

import jsonschema

from ..utils import validate_relative_path
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
            "enum": [
                "local",
                "responses_live",
                "responses_batch",
                "image_live",
                "image_batch",
            ],
        },
        "provider_endpoint": {"type": "string"},
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
        "attempt_id": {"type": "string"},
        "approval_id": {"type": "string"},
        "attempt_no": {"type": "integer", "minimum": 1, "maximum": 3},
    },
    "required": [
        "version",
        "run_id",
        "step_id",
        "task_id",
        "stage",
        "route",
        "provider_endpoint",
        "target_id",
        "target_path",
        "expected_target_hash",
        "input_projection_hash",
        "contract_name",
        "schema_hash",
        "prompt_hash",
        "model",
        "model_capability_hash",
        "policy_hash",
        "source_snapshot_hash",
        "attempt_id",
        "approval_id",
        "attempt_no",
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
    provider_endpoint: str
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
    attempt_id: str
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
    if not 1 <= value["attempt_no"] <= 3:
        raise ValueError("WORK_ORDER_V2.attempt_no musí být v rozsahu 1 až 3.")
    for key in (
        "run_id",
        "step_id",
        "task_id",
        "stage",
        "provider_endpoint",
        "target_id",
        "input_projection_hash",
        "contract_name",
        "schema_hash",
        "prompt_hash",
        "model",
        "model_capability_hash",
        "policy_hash",
        "source_snapshot_hash",
        "attempt_id",
        "approval_id",
    ):
        if not value[key]:
            raise ValueError(f"WORK_ORDER_V2.{key} nesmí být prázdné.")

    for key in ("input_projection_hash", "schema_hash", "prompt_hash", "model_capability_hash", "policy_hash", "source_snapshot_hash"):
        if re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            raise ValueError(f"WORK_ORDER_V2.{key} musí být SHA-256.")
    expected_hash = value["expected_target_hash"]
    if expected_hash is not None and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
        raise ValueError("WORK_ORDER_V2.expected_target_hash musí být SHA-256 nebo explicitní null.")
    path = value["target_path"]
    if path is not None:
        if validate_relative_path(path) != path or "\\" in path:
            raise ValueError("WORK_ORDER_V2.target_path musí být kanonická relativní cesta.")
    elif expected_hash is not None:
        raise ValueError("WORK_ORDER_V2: hash cíle bez cílové cesty.")
    if value["contract_name"] == "FILE_CONTENT_V1" and path is None:
        raise ValueError("WORK_ORDER_V2: souborový kontrakt vyžaduje cílovou cestu.")
    allowed_endpoints = {
        "local": {"local"},
        "responses_live": {"/v1/responses"},
        "responses_batch": {"/v1/batches"},
        "image_live": {"/v1/images/edits", "/v1/images/generations"},
        "image_batch": {"/v1/batches"},
    }
    if value["provider_endpoint"] not in allowed_endpoints[value["route"]]:
        raise ValueError(
            "WORK_ORDER_V2.provider_endpoint neodpovídá zvolené provider route."
        )
    if type(value["attempt_no"]) is not int or value["attempt_id"] != attempt_identity(value["run_id"], value["task_id"], value["attempt_no"]):
        raise ValueError("WORK_ORDER_V2.attempt_id neodpovídá běhu, úloze a pokusu.")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def attempt_identity(run_id: str, task_id: str, attempt_no: int) -> str:
    return "ATTEMPT-" + _hash(
        {"run_id": run_id, "task_id": task_id, "attempt_no": attempt_no}
    )[:32]


def _default_provider_endpoint(route: str, contract_name: str = "") -> str:
    if route == "responses_live":
        return "/v1/responses"
    if route in {"responses_batch", "image_batch"}:
        return "/v1/batches"
    if route == "image_live":
        return (
            "/v1/images/generations"
            if contract_name == "PROJECT_IMAGE_RESOURCE_V1"
            else "/v1/images/edits"
        )
    if route == "local":
        return "local"
    raise ValueError("WORK_ORDER_V2.route nemá známý provider endpoint.")


def work_order_from_mapping(raw_value: dict[str, Any]) -> WorkOrder:
    """Načte aktuální WorkOrder a minimálně adaptuje historický V2 záznam."""
    value = {key: item for key, item in dict(raw_value).items() if key != "order_hash"}
    legacy_endpoint = "provider_endpoint" not in value
    if legacy_endpoint:
        value["provider_endpoint"] = _default_provider_endpoint(
            str(value.get("route") or ""),
            str(value.get("contract_name") or ""),
        )
    if "attempt_id" not in value:
        value["attempt_id"] = attempt_identity(
            str(value["run_id"]),
            str(value["task_id"]),
            int(value["attempt_no"]),
        )
    # LEGACY-DATA-READER: starý V2 záznam může obsahovat odstraněný finanční identifikátor.
    value.pop("budget_reservation_id", None)
    order = WorkOrder(**value)
    validate_work_order_v2(order.to_dict())
    supplied = raw_value.get("order_hash")
    if (
        "attempt_id" in raw_value
        and not legacy_endpoint
        and supplied is not None
        and supplied != order.order_hash
    ):
        raise ValueError("WORK_ORDER_V2.order_hash neodpovídá obsahu.")
    return order


def freeze_order(config: Any, task: dict[str, Any], projection: Any) -> WorkOrder:
    """Zmrazí kanonický V2 WorkOrder před transportem."""
    stage = str(task["stage"])
    target_path = task.get("target_path")
    if target_path is not None and "expected_target_hash" not in task:
        raise ValueError("WORK_ORDER_V2: chybí explicitní původní očekávání cíle.")
    target_id = str(task.get("target_id") or target_path or stage)
    schema = copy.deepcopy(task["schema"])
    prompt = str(task["prompt"])
    model = str(task["model"])
    route = str(task.get("route") or "responses_live")
    provider_endpoint = str(
        task.get("provider_endpoint")
        or _default_provider_endpoint(route, str(task.get("contract_name") or ""))
    )
    attempt_no = task.get("attempt_no", 1)
    if type(attempt_no) is not int:
        raise ValueError("WORK_ORDER_V2.attempt_no musí být celé číslo.")
    approval_id = str(
        task.get("approval_id")
        or getattr(config, "execution_approval_id", "")
        or f"user-start:{task['run_id']}"
    )
    source_snapshot = task.get("source_snapshot") or {}
    capability = task.get("model_capability") or {}
    policy = {
        "quality": bool(getattr(config, "maximum_quality", False)),
        "auto_repair": str(getattr(config, "auto_repair", "off")),
        "verification_profile_ids": list(
            getattr(config, "verification_profile_ids", None) or []
        ),
        "stop_after_plan": bool(getattr(config, "stop_after_plan", False)),
        "dry_run": bool(getattr(config, "dry_run", False)),
        "execution": "batch" if bool(getattr(config, "send_as_c", False)) else "live",
    }
    projection_hash = _hash(projection)
    schema_hash = _hash(schema)
    prompt_hash = _hash(prompt)
    task_id = str(
        task.get("task_id")
        or (
            "TASK-"
            + _hash(
                {
                    "run_id": task["run_id"],
                    "stage": stage,
                    "target_id": target_id,
                    "projection_hash": projection_hash,
                    "schema_hash": schema_hash,
                    "prompt_hash": prompt_hash,
                    "model": model,
                }
            )[:24]
        )
    )
    order = WorkOrder(
        version=2,
        run_id=str(task["run_id"]),
        step_id=str(task["step_id"]),
        task_id=task_id,
        stage=stage,
        route=route,
        provider_endpoint=provider_endpoint,
        target_id=target_id,
        target_path=str(target_path) if target_path is not None else None,
        expected_target_hash=(
            str(task["expected_target_hash"])
            if task.get("expected_target_hash") is not None
            else None
        ),
        input_projection_hash=projection_hash,
        contract_name=str(task["contract_name"]),
        schema_hash=schema_hash,
        prompt_hash=prompt_hash,
        model=model,
        model_capability_hash=_hash(capability),
        policy_hash=_hash(policy),
        source_snapshot_hash=_hash(source_snapshot),
        attempt_id=attempt_identity(str(task["run_id"]), task_id, attempt_no),
        approval_id=approval_id,
        attempt_no=attempt_no,
    )
    validate_work_order_v2(order.to_dict())
    return order
