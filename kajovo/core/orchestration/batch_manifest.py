"""Canonical BATCH_MANIFEST_V4 identity and state transitions."""
from __future__ import annotations

import copy
from typing import Any

import jsonschema

from .contracts import canonical_sha256
from .errors import OrchestrationError

BATCH_MANIFEST_V4_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "integer", "enum": [4]},
        "run_id": {"type": "string"},
        "manifest_id": {"type": "string"},
        "route": {"type": "string", "enum": ["responses_batch", "image_batch"]},
        "model": {"type": "string"},
        "endpoint": {"type": "string", "enum": ["/v1/responses", "/v1/images/edits", "/v1/images/generations"]},
        "wave_no": {"type": "integer", "minimum": 0},
        "rows": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "custom_id": {"type": "string"},
                    "work_order_hash": {"type": "string"},
                    "target_id": {"type": "string"},
                    "target_path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "body_hash": {"type": "string"},
                    "expected_target_hash": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                },
                "required": [
                    "custom_id", "work_order_hash", "target_id", "target_path",
                    "body_hash", "expected_target_hash",
                ],
                "additionalProperties": False,
            },
        },
        "input_file_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "provider_batch_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "state": {
            "type": "string",
            "enum": [
                "prepared", "input_uploaded", "submitting", "submission_unknown",
                "submitted", "remote_terminal", "imported", "partial", "failed",
            ],
        },
    },
    "required": [
        "version", "run_id", "manifest_id", "route", "model", "endpoint",
        "wave_no", "rows", "input_file_id", "provider_batch_id", "state",
    ],
    "additionalProperties": False,
}

for _key in ("run_id", "manifest_id", "model"):
    BATCH_MANIFEST_V4_SCHEMA["properties"][_key]["pattern"] = r"\S"
for _key in ("input_file_id", "provider_batch_id"):
    BATCH_MANIFEST_V4_SCHEMA["properties"][_key]["anyOf"][0]["pattern"] = r"\S"
_row_properties = BATCH_MANIFEST_V4_SCHEMA["properties"]["rows"]["items"]["properties"]
for _key in ("custom_id", "target_id"):
    _row_properties[_key]["pattern"] = r"\S"
for _key in ("work_order_hash", "body_hash"):
    _row_properties[_key]["pattern"] = "^[0-9a-f]{64}$"
_row_properties["expected_target_hash"]["anyOf"][0]["pattern"] = "^[0-9a-f]{64}$"
_row_properties["target_path"]["anyOf"][0]["pattern"] = r"\S"


def validate_batch_manifest_v4(value: dict[str, Any]) -> None:
    try:
        jsonschema.Draft202012Validator(BATCH_MANIFEST_V4_SCHEMA).validate(value)
    except jsonschema.ValidationError as exc:
        raise OrchestrationError("BATCH_MANIFEST_INVALID", exc.message) from exc
    ids = [row["custom_id"] for row in value["rows"]]
    endpoints = {"responses_batch": {"/v1/responses"}, "image_batch": {"/v1/images/edits", "/v1/images/generations"}}
    if value["endpoint"] not in endpoints[value["route"]]:
        raise OrchestrationError("BATCH_ENDPOINT_INVALID", value["endpoint"])
    if value["state"] not in {"prepared", "failed"} and not value["input_file_id"]:
        raise OrchestrationError("BATCH_INPUT_MISSING", value["manifest_id"])
    if value["state"] in {"submitted", "remote_terminal", "imported", "partial"} and not value["provider_batch_id"]:
        raise OrchestrationError("BATCH_PROVIDER_ID_MISSING", value["manifest_id"])
    if not ids or len(ids) != len(set(ids)):
        raise OrchestrationError(
            "BATCH_CUSTOM_ID_INVALID",
            "Batch manifest vyžaduje jedinečné neprázdné položky.",
        )


def from_file_manifest(run_id: str, legacy: dict[str, Any], wave_no: int = 0) -> dict[str, Any]:
    requests = list(legacy.get("requests") or [])
    work_orders = dict(legacy.get("work_orders") or {})
    expected = dict(legacy.get("expected") or {})
    if not requests:
        raise OrchestrationError("BATCH_EMPTY", "Dávka nemá pracovní položky.")
    models = {str(row["body"].get("model") or "") for row in requests}
    if len(models) != 1:
        raise OrchestrationError("BATCH_MODEL_MIXED", "Jedna dávka vyžaduje jediný model.")
    rows: list[dict[str, Any]] = []
    for request in requests:
        custom_id = str(request["custom_id"])
        order = work_orders.get(custom_id)
        if not isinstance(order, dict):
            raise OrchestrationError(
                "BATCH_WORK_ORDER_MISSING",
                f"{custom_id}: chybí WORK_ORDER_V2.",
            )
        target = str(expected.get(custom_id) or order.get("target_id") or "")
        if not target:
            raise OrchestrationError("BATCH_TARGET_MISSING", custom_id)
        rows.append({
            "custom_id": custom_id,
            "work_order_hash": str(order.get("order_hash") or ""),
            "target_id": str(order.get("target_id") or target),
            "target_path": order.get("target_path"),
            "body_hash": canonical_sha256(request["body"]),
            "expected_target_hash": order.get("expected_target_hash"),
        })
    seed = {
        "run_id": run_id,
        "route": "responses_batch",
        "model": next(iter(models)),
        "endpoint": "/v1/responses",
        "wave_no": wave_no,
        "rows": rows,
    }
    value = {
        "version": 4,
        "run_id": run_id,
        "manifest_id": "BATCH-" + canonical_sha256(seed)[:32],
        "route": "responses_batch",
        "model": next(iter(models)),
        "endpoint": "/v1/responses",
        "wave_no": wave_no,
        "rows": rows,
        "input_file_id": None,
        "provider_batch_id": None,
        "state": "prepared",
    }
    validate_batch_manifest_v4(value)
    return value


_ALLOWED = {
    "prepared": {"input_uploaded", "failed"},
    "input_uploaded": {"submitting", "failed"},
    "submitting": {"submission_unknown", "submitted", "failed"},
    "submission_unknown": {"submitted", "remote_terminal", "failed"},
    "submitted": {"remote_terminal", "failed"},
    "remote_terminal": {"imported", "partial", "failed"},
    "imported": set(),
    "partial": {"imported"},
    "failed": set(),
}


def transition(
    manifest: dict[str, Any],
    state: str,
    *,
    input_file_id: str | None = None,
    provider_batch_id: str | None = None,
) -> dict[str, Any]:
    current = str(manifest.get("state") or "")
    if state != current and state not in _ALLOWED.get(current, set()):
        raise OrchestrationError(
            "BATCH_STATE_TRANSITION",
            f"Neplatný přechod {current} -> {state}.",
        )
    value = copy.deepcopy(manifest)
    value["state"] = state
    if input_file_id is not None:
        if value.get("input_file_id") not in (None, input_file_id):
            raise OrchestrationError("BATCH_INPUT_FILE_CONFLICT", input_file_id)
        value["input_file_id"] = input_file_id
    if provider_batch_id is not None:
        if value.get("provider_batch_id") not in (None, provider_batch_id):
            raise OrchestrationError("BATCH_PROVIDER_ID_CONFLICT", provider_batch_id)
        value["provider_batch_id"] = provider_batch_id
    validate_batch_manifest_v4(value)
    return value
