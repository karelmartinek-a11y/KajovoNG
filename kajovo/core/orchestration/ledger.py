"""Durable per-run budget ledger backed by SQLite transactions."""
from __future__ import annotations

import copy
from typing import Any

from ..context_budget import checked_measurement, enforce_budget
from ..context_compiler import content_hash
from ..contracts import ContractError
from .repository import repository_for_logger
from .work_order import WorkOrder


def _empty() -> dict[str, Any]:
    return {"version": 1, "reservations": {}, "summary": {}}


def _load(logger) -> dict[str, Any]:
    path = logger.find_json("manifests", "budget_ledger")
    if not path:
        return _empty()
    import json
    from pathlib import Path

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("reservations"), dict):
        raise ContractError(
            "Budget ledger má neplatný formát; placený submit je zablokován."
        )
    return value


def _limits(cfg) -> dict[str, Any]:
    return {
        "max_cost_microusd": getattr(cfg, "max_cost_microusd", None),
        "max_input_tokens": int(getattr(cfg, "max_input_tokens", 0)),
        "max_output_tokens": int(getattr(cfg, "max_output_tokens", 0)),
        "max_paid_requests": int(getattr(cfg, "max_paid_requests", 0)),
        "unknown_pricing": str(getattr(cfg, "unknown_pricing", "block")),
    }


def _reservation(payload: dict[str, Any], measurement: dict[str, Any]) -> dict[str, Any]:
    projected = measurement.get("projected_cost")
    usd = projected.get("usd") if isinstance(projected, dict) else None
    return {
        "request_hash": content_hash(payload),
        "model": str(payload.get("model") or ""),
        "input_tokens": int(measurement.get("input_tokens") or 0),
        "output_tokens": int(
            measurement.get("output_budget")
            or payload.get("max_output_tokens")
            or 0
        ),
        "projected_cost_microusd": (
            round(float(usd) * 1_000_000)
            if isinstance(usd, (int, float))
            else None
        ),
        "pricing_known": isinstance(usd, (int, float)),
        "measurement": copy.deepcopy(measurement),
    }


def _summarize(reservations: dict[str, Any]) -> dict[str, Any]:
    rows = list(reservations.values())
    known_cost = [
        row["projected_cost_microusd"]
        for row in rows
        if row.get("pricing_known")
    ]
    return {
        "paid_requests_reserved": len(rows),
        "input_tokens_reserved": sum(
            int(row.get("input_tokens") or 0) for row in rows
        ),
        "output_tokens_reserved": sum(
            int(row.get("output_tokens") or 0) for row in rows
        ),
        "projected_cost_microusd_known": sum(known_cost),
        "unknown_pricing_requests": sum(
            not row.get("pricing_known") for row in rows
        ),
    }


def _check(summary: dict[str, Any], limits: dict[str, Any]) -> None:
    blockers: list[str] = []
    if summary["paid_requests_reserved"] > limits["max_paid_requests"]:
        blockers.append(
            "Počet placených požadavků překračuje RUN_CONFIG_V2.max_paid_requests."
        )
    if summary["input_tokens_reserved"] > limits["max_input_tokens"]:
        blockers.append(
            "Rezervovaný vstup překračuje RUN_CONFIG_V2.max_input_tokens."
        )
    if summary["output_tokens_reserved"] > limits["max_output_tokens"]:
        blockers.append(
            "Rezervovaný výstup překračuje RUN_CONFIG_V2.max_output_tokens."
        )
    max_cost = limits["max_cost_microusd"]
    if (
        max_cost is not None
        and summary["projected_cost_microusd_known"] > max_cost
    ):
        blockers.append(
            "Projektovaná známá cena překračuje RUN_CONFIG_V2.max_cost_microusd."
        )
    if (
        summary["unknown_pricing_requests"]
        and limits["unknown_pricing"] == "block"
    ):
        blockers.append(
            "Cena alespoň jednoho požadavku není v lokálním ceníku; "
            "RUN_CONFIG_V2.unknown_pricing=block zakazuje submit."
        )
    if blockers:
        raise ContractError(" ".join(blockers))


def _reserve_sqlite(logger, cfg, work_order, reservation: dict[str, Any]) -> None:
    if work_order is None:
        raise ContractError(
            "PLACENÝ_REQUEST_BEZ_WORK_ORDER: submit nemá zmrazenou pracovní identitu."
        )
    repo = repository_for_logger(logger)
    repo.register_work_order(
        work_order,
        body_ref=reservation["request_hash"],
        input_hash=work_order.input_projection_hash,
    )
    limits = _limits(cfg)
    repo.reserve(
        reservation_id=work_order.budget_reservation_id,
        work_order_hash=work_order.order_hash,
        cost_microusd=reservation["projected_cost_microusd"],
        input_limit=reservation["input_tokens"],
        output_limit=reservation["output_tokens"],
        max_cost_microusd=limits["max_cost_microusd"],
        max_input_tokens=limits["max_input_tokens"],
        max_output_tokens=limits["max_output_tokens"],
        max_paid_requests=limits["max_paid_requests"],
    )


def reserve_paid_request(
    logger,
    cfg,
    client,
    payload: dict[str, Any],
    *,
    measurement: dict[str, Any] | None = None,
    batch: bool = False,
    key: str | None = None,
    work_order=None,
) -> dict[str, Any]:
    """Measure and transactionally reserve one request before submit."""
    if measurement is None:
        measurement = checked_measurement(payload, client, batch=batch)
    else:
        measurement = enforce_budget(copy.deepcopy(measurement))
    reservation = _reservation(payload, measurement)
    ledger = _load(logger)
    reservation_key = key or reservation["request_hash"]
    if reservation_key in ledger["reservations"]:
        return measurement

    candidate = copy.deepcopy(ledger["reservations"])
    candidate[reservation_key] = reservation
    summary = _summarize(candidate)
    limits = _limits(cfg)
    _check(summary, limits)
    _reserve_sqlite(logger, cfg, work_order, reservation)

    ledger["reservations"] = candidate
    ledger["summary"] = {**summary, "limits": limits}
    logger.save_json("manifests", "budget_ledger", ledger)
    logger.update_state({"budget_ledger_summary": ledger["summary"]})
    return measurement


def reserve_batch(
    logger,
    cfg,
    client,
    requests: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    *,
    work_orders: dict[str, Any] | None = None,
) -> None:
    """Atomically reserve the entire batch before upload/submit."""
    del client
    if len(requests) != len(measurements):
        raise ContractError(
            "BATCH budget evidence neodpovídá počtu požadavků."
        )
    work_orders = work_orders or {}
    ledger = _load(logger)
    candidate = copy.deepcopy(ledger["reservations"])
    pending: list[tuple[Any, dict[str, Any]]] = []
    for row, supplied in zip(requests, measurements, strict=True):
        body = row["body"]
        measurement = enforce_budget(copy.deepcopy(supplied))
        key = "batch:" + str(row["custom_id"])
        if key in candidate:
            continue
        reservation = _reservation(body, measurement)
        candidate[key] = reservation
        order = work_orders.get(str(row["custom_id"]))
        if order is None:
            raise ContractError(
                f"BATCH položka {row['custom_id']} nemá WORK_ORDER_V2."
            )
        pending.append((order, reservation))

    summary = _summarize(candidate)
    limits = _limits(cfg)
    _check(summary, limits)

    repo = repository_for_logger(logger)
    sql_rows: list[dict[str, Any]] = []
    for order_value, reservation in pending:
        if isinstance(order_value, WorkOrder):
            order = order_value
        else:
            value = {
                key: val for key, val in dict(order_value).items()
                if key != "order_hash"
            }
            order = WorkOrder(**value)
        repo.register_work_order(
            order,
            body_ref=reservation["request_hash"],
            input_hash=order.input_projection_hash,
        )
        sql_rows.append({
            "reservation_id": order.budget_reservation_id,
            "work_order_hash": order.order_hash,
            "cost_microusd": reservation["projected_cost_microusd"],
            "input_limit": reservation["input_tokens"],
            "output_limit": reservation["output_tokens"],
        })
    repo.reserve_many(
        sql_rows,
        max_cost_microusd=limits["max_cost_microusd"],
        max_input_tokens=limits["max_input_tokens"],
        max_output_tokens=limits["max_output_tokens"],
        max_paid_requests=limits["max_paid_requests"],
    )

    ledger["reservations"] = candidate
    ledger["summary"] = {**summary, "limits": limits}
    logger.save_json("manifests", "budget_ledger", ledger)
    logger.update_state({"budget_ledger_summary": ledger["summary"]})


def mark_submission(logger, work_order, provider_id: str | None, *, unknown: bool) -> None:
    repository_for_logger(logger).mark_submitted(
        work_order.budget_reservation_id, provider_id, unknown=unknown
    )


def release_reservation(logger, work_order) -> None:
    repository_for_logger(logger).release(work_order.budget_reservation_id)


def settle_usage(logger, work_order, response: dict[str, Any]) -> None:
    provider_id = str(response.get("id") or "")
    if not provider_id:
        return
    usage = response.get("usage") or {}
    actual_cost_microusd = None
    price_hash = None
    try:
        from ..context_pricing import PRICES, VERSION, projected_cost
        model = str(response.get("model") or work_order.model)
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            projected = projected_cost(
                model, input_tokens, output_tokens,
                batch=work_order.route == "responses_batch",
            )
            if projected and isinstance(projected.get("usd"), (int, float)):
                actual_cost_microusd = round(float(projected["usd"]) * 1_000_000)
                from .contracts import canonical_sha256
                price_hash = canonical_sha256({
                    "version": VERSION,
                    "model": model,
                    "price": PRICES.get(model),
                })
    except (TypeError, ValueError, KeyError):
        actual_cost_microusd = None
        price_hash = None
    repository_for_logger(logger).settle(
        work_order.budget_reservation_id,
        provider="openai",
        provider_item_id=provider_id,
        usage=usage if isinstance(usage, dict) else {},
        actual_cost_microusd=actual_cost_microusd,
        price_snapshot_hash=price_hash,
    )
