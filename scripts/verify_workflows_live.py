"""Živé průchody workery nad vlastními dočasnými daty; vyžaduje --live."""

from __future__ import annotations

import argparse
from dataclasses import fields
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core import pipeline, cascade_pipeline
from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.pricing import PriceTable
from kajovo.core.receipt import ReceiptDB
from kajovo.core.runlog import RunLogger
from kajovo.core.utils import new_run_id


class TrackingClient(OpenAIClient):
    def __init__(self, key):
        super().__init__(key)
        self.created_files = []
        self.created_responses = []

    def create_response(self, payload):
        result = super().create_response({**payload, "max_output_tokens": 2000})
        self.created_responses.append(result["id"])
        return result

    def upload_file(self, path, purpose="user_data"):
        result = super().upload_file(path, purpose)
        self.created_files.append(result["id"])
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        key = next(line.partition("=")[2].strip().strip("\"'") for line in
                   (Path(__file__).resolve().parents[1] / ".env.local").read_text(encoding="utf-8").splitlines()
                   if line.startswith("OPENAI_API_KEY="))
    client = TrackingClient(key)
    original, original_cascade = pipeline.OpenAIClient, cascade_pipeline.OpenAIClient
    pipeline.OpenAIClient = cascade_pipeline.OpenAIClient = lambda _: client
    try:
        with tempfile.TemporaryDirectory(prefix="kajovo-workflows-") as tmp:
            root = Path(tmp)
            settings = AppSettings(log_dir=str(root / "LOG"), cache_dir=str(root / "cache"))
            db = ReceiptDB(str(root / "receipts.sqlite"))
            for mode in ("QA", "QFILE", "GENERATE", "MODIFY"):
                values = {}
                for field in fields(pipeline.UiRunConfig):
                    annotation = str(field.type)
                    values[field.name] = (False if annotation == "bool" else [] if annotation.startswith("List") else
                                          {} if annotation.startswith("Dict") else 0.0 if annotation == "float" else "")
                out = root / mode / "out"
                inp = root / mode / "in"
                inp.mkdir(parents=True)
                if mode == "MODIFY":
                    (inp / "hello.txt").write_text("old\n", encoding="utf-8")
                values.update(project="verification", mode=mode, model="gpt-4.1-nano",
                              prompt=("Reply exactly KAJOVO-731." if mode == "QA" else
                                      "Create or update exactly one file hello.txt containing exactly hello followed by a newline. "
                                      "No other files, no README, no dependencies. Keep planning minimal."),
                              out_dir=str(out), in_dir=str(inp) if mode == "MODIFY" else "",
                              resume_files=None, resume_prev_id=None,
                              model_caps={"supports_temperature": True, "supports_previous_response_id": True})
                worker = pipeline.RunWorker(pipeline.UiRunConfig(**values), settings, key,
                                            RunLogger(settings.log_dir, new_run_id(), "verification"), db, PriceTable.builtin_fallback())
                errors, results = [], []
                worker.finished_err.connect(errors.append)
                worker.finished_ok.connect(results.append)
                worker.run()
                if errors or not results:
                    print(f"WORKFLOW {mode} FAILED: " + (errors[0] if errors else "bez výsledku"), flush=True)
                    raise RuntimeError("Pracovní postup selhal.")
                if mode == "QA":
                    assert "KAJOVO-731" in results[0]["text"]
                else:
                    assert (out / "hello.txt").read_text(encoding="utf-8") == "hello\n"
                print(f"WORKFLOW {mode} OK", flush=True)
            definition = CascadeDefinition("verification", steps=[
                CascadeStep(model="gpt-4.1-nano", input_text="Return an object with ok=true.", output_type="json",
                            output_schema_kind="custom", output_schema_custom={"type": "object", "properties": {
                                "ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}),
                CascadeStep(model="gpt-4.1-nano", input_text="Confirm this value: {{step.1.json}}",
                            previous_response_id_expr="{{step.1.response_id}}"),
            ])
            worker = cascade_pipeline.CascadeRunWorker(cascade_pipeline.CascadeRunConfig(
                "verification", definition, "", str(root / "cascade")), settings, key, db, PriceTable.builtin_fallback())
            errors, results = [], []
            worker.finished_err.connect(errors.append)
            worker.finished_ok.connect(results.append)
            worker.run()
            if errors or not results:
                raise RuntimeError("Živá kaskáda selhala.")
            print("WORKFLOW KASKADA OK", flush=True)
    finally:
        pipeline.OpenAIClient, cascade_pipeline.OpenAIClient = original, original_cascade
        failures = []
        for identifier in client.created_files:
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
        print(f"CLEANUP files={len(client.created_files)} responses={len(client.created_responses)} failures={len(failures)}", flush=True)
        if failures:
            raise RuntimeError("Neodstraněné vlastní prostředky: " + ", ".join(failures))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"LIVE_WORKFLOW_FAILED {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
