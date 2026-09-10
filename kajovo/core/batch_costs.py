"""Vyúčtování spotřeby dávek bez zápisu generovaných souborů do OUT."""
import json
import time
from decimal import Decimal
from .cost_accounting import CostLedger, Rates, calculate
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
                if model != item["model"] and not model.startswith(item["model"] + "-"):
                    rates = None
                cost = calculate(rates, body.get("usage") or {})
                if cost.total is None or item.get("tools"):
                    known = False
                else:
                    total += cost.total
                bodies.append({"custom_id": cid, "response": body})
                if body.get("id"):
                    usage = body.get("usage") or {}
                    usage = {**usage, "_requested_model": item["model"], "_reasoning": item.get("reasoning"), "_completed": body.get("status") == "completed"}
                    db.insert(Receipt(operation["scope"], time.time(), "", model, "BATCH", estimate.get("stage", "A3_FILE"),
                                      body["id"], batch["id"], usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                                      0.0, 0.0, float(cost.total) if cost.total is not None else None,
                                      bool(snapshot and snapshot.get("verified_at")), "Spotřeba dávky", {}, usage,
                                      pricing_snapshot=snapshot or {}))
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
