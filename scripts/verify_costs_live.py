"""Výslovné placené ověření počítání tokenů a malé dávky; mimo pytest."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.pricing import PriceTable
from kajovo.core.cost_accounting import CostLedger, calculate, quote
from kajovo.core.batch_costs import finalize_batches
from kajovo.core.receipt import ReceiptDB


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY") or next(line.partition("=")[2].strip().strip("\"'") for line in
        (Path(__file__).resolve().parents[1] / ".env.local").read_text(encoding="utf-8").splitlines() if line.startswith("OPENAI_API_KEY="))
    client = OpenAIClient(key)
    files, batch_id, terminal = [], None, False
    try:
        with tempfile.TemporaryDirectory(prefix="kajovo-costs-") as tmp:
            root = Path(tmp)
            table = PriceTable(str(root / "prices.json"))
            ok, detail = table.refresh_from_url("https://developers.openai.com/api/docs/pricing.md")
            assert ok, detail
            body = {"model": "gpt-4.1-nano", "input": "Reply with exactly OK.", "max_output_tokens": 32, "store": False}
            count = client.count_input_tokens(body)
            response = client.create_response(body)
            assert count == response["usage"]["input_tokens"], (count, response["usage"])
            cost = calculate(table.get(response["model"]).rates(), response["usage"])
            assert cost.total is not None
            print(f"LIVE_INPUT_COUNT_OK tokens={count} actual_usd={cost.total}", flush=True)
            db_path = str(root / "db.sqlite")
            ReceiptDB(db_path)
            ledger = CostLedger(db_path)
            item = quote(body, count, table.get(body["model"]).rates(True))
            item["custom_id"] = "cost-test"
            op = ledger.reserve("cost-test", {"items": [item], "maximum_usd": item["maximum_usd"], "stage": "COST_TEST"})
            path = root / "input.jsonl"
            path.write_text(json.dumps({"custom_id": "cost-test", "method": "POST", "url": "/v1/responses", "body": body}) + "\n", encoding="utf-8")
            uploaded = client.upload_file(str(path), purpose="batch")
            files.append(uploaded["id"])
            batch = client.create_batch(input_file_id=uploaded["id"], endpoint="/v1/responses")
            batch_id = batch["id"]
            ledger.mark(op, "pending", batch_id)
            print("BATCH_COST_TEST_SUBMITTED", flush=True)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                batch = client.retrieve_batch(batch_id)
                if batch["status"] in ("completed", "failed", "expired", "cancelled"):
                    terminal = True
                    break
                time.sleep(3)
            if not terminal:
                raise RuntimeError("Dávka není v časovém limitu dokončena.")
            files.extend(batch[k] for k in ("output_file_id", "error_file_id") if batch.get(k))
            finalize_batches(client, db_path)
            operation = ledger.operations("cost-test")[0]
            assert operation["status"] == "settled", operation["status"]
            print(f"BATCH_COST_SETTLED_OK actual_usd={operation['actual_usd']}", flush=True)
    finally:
        if batch_id and not terminal:
            client.cancel_batch(batch_id)
        for fid in files:
            client.delete_file(fid)
        client.session.close()
        if client._sdk:
            client._sdk.close()


if __name__ == "__main__":
    main()
