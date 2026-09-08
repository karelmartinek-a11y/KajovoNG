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
                  out_dir=str(tmp_path / "out"), resume_files=None, resume_prev_id=None)
    cfg = UiRunConfig(**values)
    cfg.model_caps = {"supports_temperature": True, "supports_previous_response_id": True}
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    logger = RunLogger(settings.log_dir, "RUN_090920261200_TEST", "test")
    db = ReceiptDB(str(tmp_path / "data.sqlite"))
    return RunWorker(cfg, settings, "test", logger, db, PriceTable.builtin_fallback())


def response(index, payload):
    return {"id": f"resp_{index}", "object": "response", "model": "gpt-4o-mini",
            "output_text": json.dumps(payload) if isinstance(payload, dict) else payload,
            "usage": {"input_tokens": 10, "output_tokens": 20}}


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


def test_worker_keeps_independent_settings_snapshot(tmp_path):
    worker = make_worker(tmp_path, "QA")
    settings = AppSettings()
    other = RunWorker(worker.cfg, settings, "test", worker.log, worker.db, worker.price_table)
    settings.security.allow_upload_sensitive = True
    settings.retry.max_attempts = 1
    assert not other.settings.security.allow_upload_sensitive
    assert other.settings.retry.max_attempts == 6


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
    worker = make_worker(tmp_path, "GENERATE")
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
    from kajovo.ui.batch_panel import BatchPanel
    settings = AppSettings(db_path=str(tmp_path / "data.sqlite"), cache_dir=str(tmp_path / "cache"))
    panel = BatchPanel(settings, "")
    qtbot.addWidget(panel)
    for index in [1, 1, 2]:
        panel._record_batch_receipt("batch_test", response(index, "answer"), None, "output.jsonl")
    rows = ReceiptDB(settings.db_path).query()
    assert len(rows) == 2
    assert all(row["batch_id"] == "batch_test" for row in rows)
    assert rows[0]["total_cost"] == pytest.approx(0.00000675)
