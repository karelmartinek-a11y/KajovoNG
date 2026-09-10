"""Živý GENERATE BATCH se třemi provázanými soubory a integračním testem."""

from __future__ import annotations

import argparse
from dataclasses import fields
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core import pipeline
from kajovo.core.config import AppSettings
from kajovo.core.generate_batch import process_saved_batch
from kajovo.core.pricing import PriceTable
from kajovo.core.receipt import ReceiptDB
from kajovo.core.runlog import RunLogger
from kajovo.core.utils import new_run_id
from scripts.verify_workflows_live import TrackingClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY") or next(
        line.partition("=")[2].strip().strip("\"'") for line in
        (Path(__file__).resolve().parents[1] / ".env.local").read_text(encoding="utf-8").splitlines()
        if line.startswith("OPENAI_API_KEY="))
    client = TrackingClient(key)
    original = pipeline.OpenAIClient
    pipeline.OpenAIClient = lambda _: client
    batch_id, terminal = None, False
    try:
        with tempfile.TemporaryDirectory(prefix="kajovo-generate-batch-") as tmp:
            root = Path(tmp)
            settings = AppSettings(log_dir=str(root / "LOG"), cache_dir=str(root / "cache"), db_path=str(root / "db.sqlite"))
            values = {}
            for field in fields(pipeline.UiRunConfig):
                annotation = str(field.type)
                values[field.name] = (False if annotation == "bool" else [] if annotation.startswith("List") else
                                      {} if annotation.startswith("Dict") else 0.0 if annotation == "float" else "")
            values.update(project="batch-integration", mode="GENERATE", send_as_c=True, model="gpt-4.1-nano",
                          out_dir=str(root / "out"), resume_files=None, resume_prev_id=None,
                          prompt="Create exactly three Python files, no other files or dependencies: maths.py exports "
                                 "add(a: int, b: int) -> int returning a+b; main.py imports add from maths and prints "
                                 "add(2,3) only when run as a script; test_maths.py uses unittest to verify add(2,3)==5 "
                                 "and add(-2,2)==0. Keep planning and each file short. No README or binary files.",
                          model_caps={"supports_temperature": True, "supports_previous_response_id": True})
            log = RunLogger(settings.log_dir, new_run_id(), "batch-integration")
            worker = pipeline.RunWorker(pipeline.UiRunConfig(**values), settings, key, log,
                                        ReceiptDB(settings.db_path), PriceTable.builtin_fallback())
            errors, results = [], []
            worker.finished_err.connect(errors.append)
            worker.finished_ok.connect(results.append)
            worker.run()
            if errors or not results:
                raise RuntimeError(str(errors))
            batch_id = results[0]["batch_id"]
            state = json.loads(Path(log.state_path).read_text(encoding="utf-8"))
            assert len(state["generate_batch"]["expected"]) == 3
            assert 2 <= len(client.created_responses) <= 4
            print(f"A1_A2_LIVE_OK A3_BATCH_CREATED {batch_id}", flush=True)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                batch = client.retrieve_batch(batch_id)
                if batch["status"] in ("completed", "failed", "expired", "cancelled"):
                    terminal = True
                    break
                time.sleep(3)
            if not terminal:
                raise RuntimeError("Dávka nedokončena v desetiminutovém limitu.")
            client.created_files.extend(batch[k] for k in ("output_file_id", "error_file_id") if batch.get(k))
            result = process_saved_batch(client, log.paths.run_dir, batch_id, settings)
            assert result["status"] == "files_complete_unverified", result
            # Výslovný testovací projekt se spouští pouze v dočasném adresáři.
            output = subprocess.run([sys.executable, "main.py"], cwd=root / "out", capture_output=True, text=True, timeout=30, check=True)
            assert output.stdout.strip() == "5"
            subprocess.run([sys.executable, "-m", "unittest", "discover"], cwd=root / "out", capture_output=True, text=True, timeout=30, check=True)
            print("A3_BATCH_IMPORT_OK INTEGRATION_OK", flush=True)
    finally:
        pipeline.OpenAIClient = original
        failures = []
        if batch_id and not terminal:
            client.cancel_batch(batch_id)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                batch = client.retrieve_batch(batch_id)
                if batch["status"] in ("completed", "failed", "expired", "cancelled"):
                    terminal = True
                    client.created_files.extend(batch[k] for k in ("output_file_id", "error_file_id") if batch.get(k))
                    break
                time.sleep(5)
            if not terminal:
                failures.append(batch_id)
        for identifier in set(client.created_files):
            try:
                client.delete_file(identifier)
            except Exception:
                failures.append(identifier)
        for identifier in client.created_responses:
            try:
                client._req("DELETE", f"/responses/{identifier}")
            except Exception:
                failures.append(identifier)
        client.session.close()
        if client._sdk is not None:
            client._sdk.close()
        print(f"CLEANUP failures={failures}", flush=True)
        if failures:
            raise RuntimeError("Úklid prostředků není dokončen.")


if __name__ == "__main__":
    main()
