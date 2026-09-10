"""Vyúčtování spotřeby dávek bez zápisu generovaných souborů do OUT."""
import json
import time
from decimal import Decimal
from .cost_accounting import CostLedger, Rates
from .pricing import PriceRow, price_response
from .receipt import Receipt, ReceiptDB


def finalize_batches(client, db_path):
    ledger = CostLedger(db_path)
    db = ReceiptDB(db_path)
    with ledger.connect() as con:
        pending = [dict(r) for r in con.execute("SELECT * FROM cost_operations WHERE batch_id IS NOT NULL AND status IN ('pending','unknown')")]
    for operation in pending:
        batch = client.retrieve_batch(operation["batch_id"])
        if batch.get("status") not in ("completed", "failed", "expired", "cancelled"):
            continue
        estimate = json.loads(operation["estimate_json"])
        items = estimate["items"]
        expected = {item.get("custom_id"): item for item in items}
        bodies, seen = [], set()
        total = Decimal(0)
        known = None not in expected
        for field in ("output_file_id", "error_file_id"):
            if not batch.get(field):
                continue
            for line in client.file_content(batch[field]).decode("utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                cid = row.get("custom_id")
                if cid not in expected or cid in seen:
                    known = False
                    continue
                seen.add(cid)
                body = (row.get("response") or {}).get("body") or {}
                item = expected[cid]
                snapshot = item.get("rates")
                rates = Rates(**snapshot) if snapshot else None
                model = body.get("model") or item["model"]
                # Snapshot ceny je navázán na požadavek, nikoli na aktuální cache.
                from .model_registry import model_spec
                if model != item["model"]:
                    try:
                        same = model_spec(model)["canonical"] == model_spec(item["model"])["canonical"]
                    except ValueError:
                        same = False
                    if not same:
                        rates = None
                row_price = PriceRow(model, 0, 0, file_search_per_1k=item.get("file_search_per_1k")) if rates else None
                cost, tool_cost, rates, reason = price_response(row_price, body, batch=True, rates=rates)
                rejected = (row.get("response") or {}).get("status_code") in (400, 401, 403, 404, 422) and not body.get("usage")
                if cost is None and not rejected:
                    known = False
                elif cost is not None:
                    total += cost
                bodies.append({"custom_id": cid, "response": body})
                if body.get("id"):
                    usage = body.get("usage") or {}
                    usage = {**usage, "_requested_model": item["model"], "_reasoning": item.get("reasoning"), "_completed": body.get("status") == "completed",
                        "_pricing_reason": reason, "_service_tier": body.get("service_tier"), "_regional": item.get("regional", False),
                        "_file_search_calls": sum(o.get("type") == "file_search_call" for o in body.get("output", []))}
                    db.insert(Receipt(operation["scope"], time.time(), "", model, "BATCH", estimate.get("stage", "A3_FILE"),
                                      body["id"], batch["id"], usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                                      float(tool_cost) if tool_cost is not None else None, 0.0, float(cost) if cost is not None else None,
                                      bool(snapshot and snapshot.get("verified_at")), "Spotřeba dávky", {}, usage,
                                      pricing_snapshot=rates.snapshot() if rates else {}))
        known = known and seen == set(expected)
        ledger.settle(operation["id"], total if known else None, usage=bodies, source=items)
        if not known:
            # Terminální neúplný výsledek se znovu nestahuje při každém pollu.
            with ledger.connect() as con:
                con.execute("UPDATE cost_operations SET status='terminal_unknown' WHERE id=?", (operation["id"],))
    with ledger.connect() as con:
        return [dict(r) for r in con.execute("SELECT DISTINCT scope,id FROM cost_operations WHERE batch_id IS NOT NULL AND status IN ('settled','terminal_unknown') AND seen=0")]


def mark_seen(db_path, operations):
    ledger = CostLedger(db_path)
    with ledger.connect() as con:
        for op in operations:
            con.execute("UPDATE cost_operations SET seen=1 WHERE id=?", (op,))
