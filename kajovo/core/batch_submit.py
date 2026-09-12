"""Jednoznačná hranice mezi ověřením BATCH a pracovním POST /batches."""
from __future__ import annotations

from typing import Any, Iterable


def submit_verified_batch(client, input_file_id: str, rows: Iterable[dict[str, Any]], *, endpoint: str = "/v1/responses", completion_window: str = "24h") -> dict[str, Any]:
    """Provede právě jeden pracovní POST bez dalšího preflightu a bez retry.

    Volající smí tuto funkci použít až poté, co `upload_file(..., purpose="batch")`
    dokončil kanonický BATCH preflight. `_req` má pro POST /batches právě jeden pokus.
    """
    client._validate_resource_id(input_file_id)
    if endpoint != "/v1/responses" or completion_window != "24h":
        raise ValueError("Program podporuje pouze Batch Responses s oknem 24h.")
    body = {"input_file_id": input_file_id, "endpoint": endpoint, "completion_window": completion_window}
    result = client._req("POST", "/batches", json_body=body)
    policy = getattr(client, "_policy", None)
    if policy is not None:
        for row in rows:
            payload = row.get("body") if isinstance(row, dict) else None
            if isinstance(payload, dict):
                policy.proofs.pop(policy.key(payload, True), None)
        policy.save()
    return result


def exact_batch_matches(records: Iterable[dict[str, Any]], input_file_id: str, endpoint: str = "/v1/responses") -> list[dict[str, Any]]:
    """Najde pouze přesnou korelaci již vzniklé pracovní dávky."""
    return [
        record for record in records
        if isinstance(record, dict)
        and record.get("input_file_id") == input_file_id
        and (record.get("endpoint") or "/v1/responses") == endpoint
    ]
