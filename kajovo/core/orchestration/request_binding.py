"""Vazba skutečného Responses payloadu na kanonický WorkOrder před síťovou operací."""
from __future__ import annotations

from typing import Any

from ..contracts import ContractError
from ..structured_output import file_content_format, prepare_payload
from .contracts import canonical_sha256
from .work_order import WorkOrder


def validate_response_work_order(order: WorkOrder, payload: dict[str, Any]) -> None:
    try:
        order.to_dict()
        prepare_payload(payload)
    except (TypeError, ValueError) as exc:
        raise ContractError("REQUEST_WORK_ORDER_INVALID: " + str(exc)) from exc
    if order.route not in {"responses_live", "responses_batch"}:
        raise ContractError("REQUEST_ROUTE_MISMATCH: Responses vyžaduje Responses WorkOrder.")
    if payload.get("model") != order.model:
        raise ContractError("REQUEST_MODEL_MISMATCH: payload změnil zmrazený model.")
    fmt = payload["text"]["format"]
    if fmt["name"] != order.contract_name:
        raise ContractError("REQUEST_CONTRACT_MISMATCH: payload změnil název kontraktu.")
    if canonical_sha256(fmt["schema"]) != order.schema_hash:
        raise ContractError("REQUEST_SCHEMA_MISMATCH: payload změnil JSON masku.")
    if order.contract_name == "FILE_CONTENT_V1" and fmt["schema"] != file_content_format()["format"]["schema"]:
        raise ContractError("REQUEST_FILE_SCHEMA_MISMATCH: model nesmí přepisovat cestu cíle.")
    if {str(key).casefold() for key in payload} & {"authorization", "headers", "api_key", "password", "ssh_password"}:
        raise ContractError("REQUEST_CREDENTIAL_FIELD: runtime tajemství nesmí být částí payloadu.")
