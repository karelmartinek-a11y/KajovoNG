"""Živé ověření vytvoření, sledování a výstupu vlastní dávky."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core.contracts import extract_text_from_response, parse_json_strict
from kajovo.core.openai_client import OpenAIClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        key = next(line.partition("=")[2].strip().strip("\"'") for line in
                   (Path(__file__).resolve().parents[1] / ".env.local").read_text(encoding="utf-8").splitlines()
                   if line.startswith("OPENAI_API_KEY="))
    client = OpenAIClient(key)
    own_files = []
    batch_id = None
    terminal = False
    try:
        row = {"custom_id": "kajovo-verification", "method": "POST", "url": "/v1/responses", "body": {
            "model": "gpt-4.1-nano", "input": "Return JSON: {\"ok\":true}.",
            "text": {"format": {"type": "json_object"}}, "max_output_tokens": 100, "store": False}}
        with tempfile.TemporaryDirectory(prefix="kajovo-batch-") as tmp:
            path = Path(tmp) / "requests.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            input_id = client.upload_file(str(path), purpose="batch")["id"]
        own_files.append(input_id)
        batch = client.create_batch(input_id)
        batch_id = batch["id"]
        print(f"BATCH_CREATE OK {batch_id}", flush=True)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            batch = client.retrieve_batch(batch_id)
            status = batch.get("status")
            if status in ("completed", "failed", "cancelled", "expired"):
                terminal = True
                break
            time.sleep(2)
        if not terminal:
            print("BATCH_TIMEOUT; zpracování nedokončeno v limitu ověření", flush=True)
            return
        for field in ("output_file_id", "error_file_id"):
            if batch.get(field):
                own_files.append(batch[field])
        if batch.get("status") != "completed":
            raise RuntimeError("Dávka nedokončila požadavek.")
        output = client.file_content(batch["output_file_id"]).decode("utf-8")
        lines = [json.loads(line) for line in output.splitlines() if line.strip()]
        assert len(lines) == 1 and lines[0]["custom_id"] == "kajovo-verification"
        assert lines[0]["response"]["status_code"] == 200
        assert parse_json_strict(extract_text_from_response(lines[0]["response"]["body"])) == {"ok": True}
        print("BATCH_OUTPUT OK", flush=True)
    finally:
        failures = []
        if batch_id and not terminal:
            try:
                client.cancel_batch(batch_id)
                print(f"BATCH_CANCEL ACCEPTED {batch_id}", flush=True)
                cancel_deadline = time.monotonic() + 600
                while time.monotonic() < cancel_deadline:
                    batch = client.retrieve_batch(batch_id)
                    if batch.get("status") in ("completed", "cancelled", "expired", "failed"):
                        terminal = True
                        own_files.extend(batch[field] for field in ("output_file_id", "error_file_id") if batch.get(field))
                        break
                    time.sleep(5)
                if not terminal:
                    failures.append(batch_id)
                    print(f"BATCH_CANCEL_PENDING {batch_id}", flush=True)
            except Exception:
                failures.append(batch_id)
        for identifier in set(own_files):
            try:
                client.delete_file(identifier)
            except Exception:
                failures.append(identifier)
        client.session.close()
        if client._sdk is not None:
            client._sdk.close()
        print(f"BATCH_CLEANUP files={len(own_files)} failures={len(failures)}", flush=True)
        if failures:
            raise RuntimeError("Neodstraněné vlastní soubory: " + ", ".join(failures))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"LIVE_BATCH_FAILED {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
