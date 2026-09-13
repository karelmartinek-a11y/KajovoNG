"""Jednoznačná hranice mezi lokální validací BATCH a pracovním POST /batches."""
from __future__ import annotations

from typing import Any, Iterable

from .request_rules import validate_response_payload


def submit_verified_batch(
    client,
    input_file_id: str,
    rows: Iterable[dict[str, Any]],
    *,
    endpoint: str = "/v1/responses",
    completion_window: str = "24h",
) -> dict[str, Any]:
    """Provede právě jeden pracovní POST /batches bez pomocné zkušební dávky.

    Volající smí tuto funkci použít až poté, co byl pracovní JSONL lokálně
    zvalidován a nahrán jako skutečný vstup dávky. Žádný samostatný testovací
    soubor ani testovací POST /batches se před tímto voláním nevytváří.
    Transport `_req` má pro pracovní POST /batches právě jeden pokus.
    """
    client._validate_resource_id(input_file_id)
    if endpoint != "/v1/responses" or completion_window != "24h":
        raise ValueError("Program podporuje pouze Batch Responses s oknem 24h.")
    verified_rows = list(rows)
    if not verified_rows:
        raise ValueError("Pracovní dávka neobsahuje žádný lokálně ověřený požadavek.")
    ids = set()
    models = set()
    for row in verified_rows:
        cid = row.get("custom_id")
        if not isinstance(cid, str) or not cid or cid in ids:
            raise ValueError("Pracovní dávka vyžaduje jedinečná neprázdná ID úloh.")
        ids.add(cid)
        if row.get("method") != "POST" or row.get("url") != endpoint:
            raise ValueError("Úloha dávky musí používat POST na její endpoint.")
        validate_response_payload(row["body"], batch=True)
        models.add(row["body"]["model"])
    if len(models) != 1:
        raise ValueError("Pracovní dávka vyžaduje jediný model.")
    # OpenAIClient dostane přesně řádky z pracovního JSONL, které již prošly
    # deterministickou lokální validací. create_batch proto neprovádí žádný
    # další placený test a zachovává jedinou veřejnou transportní cestu.
    return client.create_batch(
        input_file_id=input_file_id,
        endpoint=endpoint,
        completion_window=completion_window,
        _prevalidated_rows=verified_rows,
    )


def exact_batch_matches(
    records: Iterable[dict[str, Any]],
    input_file_id: str,
    endpoint: str = "/v1/responses",
) -> list[dict[str, Any]]:
    """Najde pouze přesnou korelaci již vzniklé pracovní dávky."""
    return [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("input_file_id") == input_file_id
        and (record.get("endpoint") or "/v1/responses") == endpoint
    ]
