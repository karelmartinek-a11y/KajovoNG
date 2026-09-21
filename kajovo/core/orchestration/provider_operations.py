"""Nefinanční evidence provider operací, idempotence a raw usage telemetry."""
from __future__ import annotations

import copy
from typing import Any

from ..context_compiler import content_hash
from ..context_limits import checked_measurement, ensure_technical_limits
from ..contracts import ContractError
from .contracts import canonical_sha256
from .repository import repository_for_logger
from .request_binding import validate_response_work_order
from .run_config import build_run_config_v2, run_scope_hash
from .work_order import WorkOrder, work_order_from_mapping


def _policy(run_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality": run_config["quality"],
        "execution": run_config["execution"],
        "auto_repair": run_config["auto_repair"],
        "verification_profile_ids": run_config["verification_profile_ids"],
        "stop_after_plan": run_config["stop_after_plan"],
        "dry_run": run_config["dry_run"],
    }


def _ensure_run(logger, cfg, work_order: WorkOrder) -> None:
    repo = repository_for_logger(logger)
    if repo.has_run(work_order.run_id):
        return
    run_config = build_run_config_v2(cfg)
    repo.register_run(
        work_order.run_id,
        lineage_id=work_order.run_id,
        scope_hash=run_scope_hash(cfg),
        policy_hash=canonical_sha256(_policy(run_config)),
        config=run_config,
        approval_id=work_order.approval_id,
        status="running",
    )


def _endpoint(order: WorkOrder) -> str:
    if order.route in {"responses_batch", "image_batch"}:
        return "/v1/batches"
    if order.route == "responses_live":
        return "/v1/responses"
    raise ContractError("Obrazová/local operace vyžaduje vlastní explicitní endpoint.")


def prepare_provider_request(
    logger,
    cfg,
    client,
    payload: dict[str, Any],
    *,
    measurement: dict[str, Any] | None = None,
    batch: bool = False,
    work_order: WorkOrder | None = None,
    allow_existing: bool = False,
) -> dict[str, Any]:
    """Ověří technické limity a připraví idempotentní provider operation."""
    if work_order is None:
        raise ContractError(
            "PROVIDER_REQUEST_BEZ_WORK_ORDER: submit nemá zmrazenou pracovní identitu."
        )
    validate_response_work_order(work_order, payload)
    if measurement is None:
        measurement = checked_measurement(payload, client, batch=batch)
    else:
        measurement = ensure_technical_limits(copy.deepcopy(measurement))

    _ensure_run(logger, cfg, work_order)
    repo = repository_for_logger(logger)
    persisted_hash = repo.register_work_order(
        work_order,
        body_ref=content_hash(payload),
        input_hash=work_order.input_projection_hash,
    )
    repo.prepare_provider_operation(
        attempt_id=work_order.attempt_id,
        work_order_hash=persisted_hash,
        endpoint=_endpoint(work_order),
        request_hash=content_hash(payload),
        allow_existing=allow_existing,
    )
    return measurement


def prepare_batch(
    logger,
    cfg,
    requests: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    *,
    work_orders: dict[str, Any],
) -> None:
    if len(requests) != len(measurements):
        raise ContractError(
            "BATCH technická evidence neodpovídá počtu požadavků."
        )
    # Celá sada musí být platná před prvním zápisem či zahájením operace.
    identifiers = [str(row.get("custom_id") or "") for row in requests]
    if not identifiers or "" in identifiers or len(set(identifiers)) != len(identifiers) or set(identifiers) != set(work_orders):
        raise ContractError("BATCH custom_id a WorkOrder mapování nejsou vzájemně jednoznačné.")
    for row, supplied in zip(requests, measurements, strict=True):
        raw_order = work_orders[row["custom_id"]]
        order = raw_order if isinstance(raw_order, WorkOrder) else work_order_from_mapping(raw_order)
        if row.get("method") != "POST" or row.get("url") != "/v1/responses" or order.route != "responses_batch":
            raise ContractError("BATCH řádek neodpovídá transportní cestě WorkOrderu.")
        validate_response_work_order(order, row["body"])
        ensure_technical_limits(copy.deepcopy(supplied))
    repo = repository_for_logger(logger)
    for row, supplied in zip(requests, measurements, strict=True):
        custom_id = str(row["custom_id"])
        raw_order = work_orders.get(custom_id)
        if raw_order is None:
            raise ContractError(f"BATCH položka {custom_id} nemá WORK_ORDER_V2.")
        order = (
            raw_order
            if isinstance(raw_order, WorkOrder)
            else work_order_from_mapping(raw_order)
        )
        ensure_technical_limits(copy.deepcopy(supplied))
        _ensure_run(logger, cfg, order)
        persisted_hash = repo.register_work_order(
            order,
            body_ref=content_hash(row["body"]),
            input_hash=order.input_projection_hash,
        )
        repo.prepare_provider_operation(
            attempt_id=order.attempt_id,
            work_order_hash=persisted_hash,
            endpoint=_endpoint(order),
            request_hash=content_hash(row["body"]),
        )


def mark_submission_started(logger, work_order: WorkOrder) -> None:
    repository_for_logger(logger).mark_submission_started(work_order.attempt_id)


def mark_submission(
    logger,
    work_order: WorkOrder,
    provider_id: str | None,
    *,
    unknown: bool,
) -> None:
    repository_for_logger(logger).mark_submitted(
        work_order.attempt_id,
        provider_id,
        unknown=unknown,
    )


def mark_not_submitted(logger, work_order: WorkOrder) -> None:
    repository_for_logger(logger).mark_not_submitted(work_order.attempt_id)


def set_remote_input_file(
    logger,
    work_order: WorkOrder,
    file_id: str,
) -> None:
    repository_for_logger(logger).set_remote_input_file(
        work_order.attempt_id,
        file_id,
    )


def record_usage(logger, work_order: WorkOrder, response: dict[str, Any]) -> None:
    provider_id = str(response.get("id") or "")
    if not provider_id:
        return
    usage = response.get("usage")
    repository_for_logger(logger).record_usage(
        work_order.attempt_id,
        provider="openai",
        provider_item_id=provider_id,
        usage=usage if isinstance(usage, dict) else {},
        raw_response_ref="response:" + provider_id,
    )
