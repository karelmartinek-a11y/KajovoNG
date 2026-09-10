import json
from dataclasses import fields
from unittest.mock import Mock, patch

import pytest

from kajovo.core.config import AppSettings
from kajovo.core.pipeline import UiRunConfig, RunWorker
from kajovo.core.pricing import PriceTable
from kajovo.core.receipt import ReceiptDB
from kajovo.core.runlog import RunLogger


def make_worker(tmp_path, mode):
    values = {}
    for field in fields(UiRunConfig):
        annotation = str(field.type)
        values[field.name] = (False if annotation == "bool" else [] if annotation.startswith("List")
                              else {} if annotation.startswith("Dict") else 0.0 if annotation == "float" else "")
    values.update(project="test", prompt="Write a file", mode=mode, model="gpt-4o-mini",
                  model_a1="gpt-4o-mini", model_a2="gpt-4o-mini", model_a3="gpt-4o-mini",
                  out_dir=str(tmp_path / "out"), resume_files=None, resume_prev_id=None,
                  available_models=["gpt-4o-mini"])
    cfg = UiRunConfig(**values)
    cfg.model_caps = {"ok_basic": True, "supports_temperature": True, "supports_previous_response_id": True}
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    logger = RunLogger(settings.log_dir, "RUN_090920261200_TEST", "test")
    db = ReceiptDB(str(tmp_path / "data.sqlite"))
    return RunWorker(cfg, settings, "test", logger, db, PriceTable.builtin_fallback())


def response(index, payload):
    # Úplné vzorky API pro testy orchestrace; záměrné vady souborů zůstávají zachované.
    if isinstance(payload, str):
        payload = {"text": payload}
    elif payload.get("contract") == "A1_PLAN":
        payload = {"project": {k: "test" for k in ("name", "one_liner", "target_os", "language", "runtime")},
                   "assumptions": [], "requirements": {k: [] for k in ("functional", "non_functional", "constraints")},
                   "architecture": {k: [] for k in ("modules", "data_flow", "error_handling", "security_notes")},
                   "build_run": {k: [] for k in ("prerequisites", "commands", "verification")},
                   "deliverable_policy": {"max_lines_per_chunk": 500}, **payload}
    elif payload.get("contract") == "B1_PLAN":
        payload = {"diagnosis": {"summary": "test", "evidence": [], "likely_root_causes": []},
                   "change_plan": {k: [] for k in ("goals", "files_to_modify", "files_to_add", "verification_steps")},
                   "missing_inputs": [], **payload}
    elif payload.get("contract") == "A2_STRUCTURE" and "version" not in payload:
        payload = {"version": 2, "rules": [], "packages": [], "interfaces": [], **payload}
        payload["files"] = [{"purpose": "test", "language": "text", "kind": "text", "dependencies": [],
                             "provides": [], "requires": [], "behavior": "test", **item} for item in payload["files"]]
    elif payload.get("contract") == "B2_STRUCTURE":
        payload = {**payload, "touched_files": [{"intent": "test", **item} for item in payload["touched_files"]]}
    return {"id": f"resp_{index}", "object": "response", "model": "gpt-4o-mini", "status": "completed",
            "output_text": json.dumps(payload), "usage": {"input_tokens": 10, "output_tokens": 20}}


@pytest.mark.parametrize("mode", ["QA", "QFILE", "GENERATE", "MODIFY"])
def test_complete_offline_workflow(tmp_path, mode):
    worker = make_worker(tmp_path, mode)
    file = {"contract": "A3_FILE", "path": "hello.txt", "content": "hello\n",
            "chunking": {"chunk_index": 0, "chunk_count": 1, "has_more": False, "next_chunk_index": None}}
    payloads = ["answer"] if mode == "QA" else [file]
    if mode == "GENERATE":
        payloads = [{"contract": "A1_PLAN"}, {"contract": "A2_STRUCTURE", "files": [{"path": "hello.txt", "purpose": "test"}]}, file]
    if mode == "MODIFY":
        in_dir = tmp_path / "in"
        in_dir.mkdir()
        worker.cfg.in_dir = str(in_dir)
        file["contract"] = "B3_FILE"
        file["action"] = "add"
        payloads = [{"contract": "B1_PLAN"}, {"contract": "B2_STRUCTURE", "touched_files": [{"path": "hello.txt", "action": "add"}]}, file]
    client = Mock()
    client.upload_file.return_value = {"id": "file_test"}
    client.retrieve_file.return_value = {"id": "file_test", "filename": "input.txt", "bytes": 100}
    client.create_response.side_effect = [response(index, item) for index, item in enumerate(payloads)]
    errors, results = [], []
    worker.finished_err.connect(errors.append)
    worker.finished_ok.connect(results.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert len(results) == 1
    assert len(worker.db.query()) == len(payloads)
    assert client.create_response.call_count == len(payloads)
    if mode != "QA":
        assert (tmp_path / "out" / "hello.txt").read_text(encoding="utf-8") == "hello\n"
    assert json.loads((tmp_path / "LOG" / worker.log.run_id / "run_state.json").read_text())["status"] == "completed"


def test_receipt_disk_error_does_not_repeat_api(tmp_path):
    worker = make_worker(tmp_path, "QA")
    client = Mock()
    client.create_response.return_value = response(1, "answer")
    with patch.object(worker.db, "insert", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            worker._create_response(client, {"model": "gpt-4o-mini", "input": "test"})
    assert client.create_response.call_count == 1


def test_incomplete_response_records_usage_but_stops_workflow(tmp_path):
    worker = make_worker(tmp_path, "QA")
    client = Mock()
    client.create_response.return_value = {**response(1, "partial"), "status": "incomplete"}
    from kajovo.core.contracts import ContractError
    with pytest.raises(ContractError):
        worker._create_response(client, {"model": "gpt-4o-mini", "input": "test"})
    assert len(worker.db.query()) == 1
    assert client.create_response.call_count == 1


def test_premature_file_termination_preserves_output(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    out = tmp_path / "out"
    out.mkdir()
    target = out / "hello.txt"
    target.write_text("original", encoding="utf-8")
    payloads = [{"contract": "A1_PLAN"}, {"contract": "A2_STRUCTURE", "files": [{"path": "hello.txt", "purpose": "test"}]},
                {"contract": "A3_FILE", "path": "hello.txt", "content": "partial",
                 "chunking": {"chunk_index": 0, "chunk_count": 3, "has_more": False, "next_chunk_index": None}}]
    client = Mock()
    client.create_response.side_effect = [response(i, item) for i, item in enumerate(payloads)]
    errors, results = [], []
    worker.finished_err.connect(errors.append)
    worker.finished_ok.connect(results.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert errors and not results
    assert target.read_text(encoding="utf-8") == "original"


def test_worker_keeps_independent_settings_snapshot(tmp_path):
    worker = make_worker(tmp_path, "QA")
    settings = AppSettings()
    other = RunWorker(worker.cfg, settings, "test", worker.log, worker.db, worker.price_table)
    settings.security.allow_upload_sensitive = True
    settings.retry.max_attempts = 1
    assert not other.settings.security.allow_upload_sensitive
    assert other.settings.retry.max_attempts == 6


@pytest.mark.parametrize("invalid", [{}, {"content": None}, {"content": 7}])
def test_output_manifest_requires_text_before_any_write(tmp_path, invalid):
    from kajovo.core.contracts import ContractError

    worker = make_worker(tmp_path, "GENERATE")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / "keep.txt"
    target.write_text("zachovat", encoding="utf-8")
    with pytest.raises(ContractError):
        worker._save_out_files([
            {"path": "new.txt", "content": "nový soubor"},
            {"path": "keep.txt", **invalid},
        ])
    assert target.read_text(encoding="utf-8") == "zachovat"
    assert not (out_dir / "new.txt").exists()


def test_output_manifest_accepts_explicit_empty_text(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker._save_out_files([{"path": "empty.txt", "content": ""}])
    assert (tmp_path / "out" / "empty.txt").read_bytes() == b""


def test_worker_keeps_independent_run_configuration(tmp_path):
    worker = make_worker(tmp_path, "QA")
    other = RunWorker(worker.cfg, AppSettings(), "test", worker.log, worker.db, worker.price_table)
    worker.cfg.out_dir = "jiný-výstup"
    worker.cfg.model_caps["supports_previous_response_id"] = False
    worker.cfg.attached_file_ids.append("file_changed")
    assert other.cfg.out_dir == str(tmp_path / "out")
    assert other.cfg.model_caps["supports_previous_response_id"]
    assert not other.cfg.attached_file_ids


def test_custom_cascade_records_each_step(tmp_path):
    from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
    from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    db = ReceiptDB(str(tmp_path / "receipts.sqlite"))
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-4o-mini", input_text="test")])
    cfg = CascadeRunConfig("project", definition, "", str(tmp_path / "out"), run_id="RUN_090920261200_TEST")
    worker = CascadeRunWorker(cfg, settings, "test", db, PriceTable.builtin_fallback())
    client = Mock()
    client.create_response.return_value = response(1, "answer")
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert results[0]["run_id"] == cfg.run_id
    assert len(db.query()) == 1


def test_batch_uses_only_supported_jsonl_fields(tmp_path):
    worker = make_worker(tmp_path, "MODIFY")
    worker.cfg.send_as_c = True
    client = Mock()
    client.upload_file.return_value = {"id": "file_batch"}
    client.create_batch.return_value = {"id": "batch_test"}
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert results[0]["batch_id"] == "batch_test"
    path = client.upload_file.call_args.args[0]
    with open(path, encoding="utf-8") as handle:
        request = json.loads(handle.readline())
    assert set(request) == {"custom_id", "method", "url", "body"}
    client.create_response.assert_not_called()


def test_batch_receipts_count_distinct_responses_once(tmp_path, qtbot):
    from kajovo.desktop.batches import BatchPanel
    settings = AppSettings(db_path=str(tmp_path / "data.sqlite"), cache_dir=str(tmp_path / "cache"))
    panel = BatchPanel(settings, "")
    qtbot.addWidget(panel)
    for index in [1, 1, 2]:
        panel._record_batch_receipt("batch_test", response(index, "answer"), None, "output.jsonl")
    rows = ReceiptDB(settings.db_path).query()
    assert len(rows) == 2
    assert all(row["batch_id"] == "batch_test" for row in rows)
    assert rows[0]["total_cost"] == pytest.approx(0.00000675)
