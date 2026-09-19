"""Common request construction and provider response classification."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .errors import OrchestrationError


@dataclass(frozen=True)
class PreparedRequest:
    body: dict[str, Any]
    route: str
    work_order_hash: str


@dataclass(frozen=True)
class ClassifiedResponse:
    kind: str
    response: dict[str, Any]


def build_payload(order, projection, attempt) -> PreparedRequest:
    """Build immutable transport payload from a frozen WorkOrder and Projection."""
    body = copy.deepcopy(getattr(attempt, "body", attempt))
    if not isinstance(body, dict):
        raise OrchestrationError("REQUEST_INVALID", "Prepared request body musí být objekt.")
    if body.get("model") != order.model:
        raise OrchestrationError("MODEL_BINDING_CHANGED", "Model se po schválení změnil.")
    if body.get("previous_response_id") and order.route != "responses_live":
        raise OrchestrationError(
            "CONVERSATION_ROUTE_INVALID",
            "previous_response_id je povoleno pouze pro explicitní LIVE conversation route.",
        )
    if order.route == "responses_batch":
        for forbidden in ("previous_response_id", "conversation", "background", "tools", "service_tier"):
            if forbidden in body:
                raise OrchestrationError(
                    "BATCH_FIELD_FORBIDDEN",
                    f"Batch row nesmí obsahovat {forbidden}.",
                )
    return PreparedRequest(body, order.route, order.order_hash)


def classify_response(raw: dict[str, Any]) -> ClassifiedResponse:
    if not isinstance(raw, dict):
        raise OrchestrationError("PROVIDER_RESPONSE_INVALID", "Provider response musí být objekt.")
    status = raw.get("status")
    if status in {"queued", "in_progress"}:
        return ClassifiedResponse("remote_pending", raw)
    if status == "incomplete":
        return ClassifiedResponse("incomplete", raw)
    if status in {"failed", "cancelled", "expired"}:
        return ClassifiedResponse("remote_failed", raw)
    if status != "completed":
        raise OrchestrationError("PROVIDER_STATUS_UNKNOWN", str(status))

    output = raw.get("output")
    output = output if isinstance(output, list) else []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            return ClassifiedResponse("tool_calls", raw)
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and content.get("type") == "refusal":
                return ClassifiedResponse("refusal", raw)
    if isinstance(raw.get("output_text"), str):
        return ClassifiedResponse("final_text", raw)
    for item in output:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                return ClassifiedResponse("final_text", raw)
    raise OrchestrationError(
        "PROVIDER_OUTPUT_MISSING",
        "Completed response nemá finální textový výstup.",
    )
