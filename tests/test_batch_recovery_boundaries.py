"""Obnova dávek nesmí opakovat submit ani zaměnit vzdálený stav za dodávku."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from kajovo.core.batch_completion import complete_saved_batch, read_state
from kajovo.core.batch_submit import submit_verified_batch
from kajovo.core.config import AppSettings
from kajovo.core.contracts import ContractError
from kajovo.core.openai_client import OpenAIError
from kajovo.core.orchestration.repository import OrchestrationRepository
from kajovo.core.photo_batch import (
    IMAGE_EDIT_ENDPOINT,
    download_results,
    image_edit_model_ids,
    new_job,
    prepare_and_submit,
    refresh_job,
    save_job,
)
from kajovo.core.runlog import RunLogger
from kajovo.core.runs.batch_execution import _work_orders
from kajovo.core.structured_output import text_format
from test_generate_batch import manifest
from test_workflows import make_worker


def _legacy_run(tmp_path):
    logger = RunLogger(str(tmp_path / "LOG"), "RUN_RECOVERY", "Obnova")
    logger.update_state({
        "batch_id": "batch_saved", "status": "batch_pending",
        "out_dir": str(tmp_path / "out"),
    })
    return logger


def _jsonl(row):
    return (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")


def _no_submit(client):
    client.create_response.assert_not_called()
    client.create_batch.assert_not_called()
    client.upload_file.assert_not_called()


def test_legacy_completion_archives_raw_and_output_and_checkpoint(tmp_path):
    logger = _legacy_run(tmp_path)
    client = Mock()
    client.retrieve_batch.return_value = {
        "id": "batch_saved", "status": "completed", "output_file_id": "file_output",
    }
    raw = _jsonl({
        "custom_id": "RUN_RECOVERY_C1", "response": {"status_code": 200, "body": {
            "status": "completed", "output_text": json.dumps({
                "contract": "C_FILES_ALL", "root": "src",
                "files": [{"path": "hello.txt", "purpose": "Výsledek", "content": "Přesný výsledek\n"}],
                "project": {"name": "Test", "target_os": "Windows", "runtime": "text", "language": "text"},
                "build_run": {"prerequisites": [], "commands": [], "verification": []}, "notes": [],
            }),
        }},
    })
    client.file_content.return_value = raw
    events = []
    result = complete_saved_batch(
        client, logger.paths.run_dir, "batch_saved", AppSettings(), progress=events.append,
    )
    target = tmp_path / "out" / "src" / "hello.txt"
    assert target.read_text("utf-8") == "Přesný výsledek\n"
    assert result["status"] == "files_complete_unverified"
    assert result["written"] == [str(target)]
    state = read_state(logger.paths.run_dir)
    assert state["batch_imports"]["batch_saved"]["import_status"] == result["status"]
    assert state["batch_records"]["batch_saved"] == client.retrieve_batch.return_value
    assert (Path(logger.paths.responses_dir) / "batch_saved_output_file_id.jsonl").read_bytes() == raw
    artifacts = logger.bundle.artifacts()
    assert any(row["kind"] == "batch_jsonl" for row in artifacts)
    assert any(row["kind"] == "output_file" for row in artifacts)
    assert any(row["checkpoint_type"] == "files_downloaded_validated" for row in logger.bundle.checkpoints())
    assert events[-1].stage == "Aktualizace evidence"
    _no_submit(client)


@pytest.mark.parametrize("status", ["failed", "expired", "cancelled"])
def test_legacy_terminal_error_is_archived_without_overwriting_out(tmp_path, status):
    logger = _legacy_run(tmp_path)
    target = tmp_path / "out" / "user.txt"
    target.parent.mkdir()
    target.write_text("Uživatelský obsah", encoding="utf-8")
    client = Mock()
    client.retrieve_batch.return_value = {
        "id": "batch_saved", "status": status, "error_file_id": "file_errors",
    }
    raw = _jsonl({"custom_id": "RUN_RECOVERY_C1", "error": {"code": "batch_expired"}})
    client.file_content.return_value = raw
    result = complete_saved_batch(client, logger.paths.run_dir, "batch_saved", AppSettings())
    assert result["status"] == "partial" and result["errors"]
    assert result["written"] == []
    assert target.read_text("utf-8") == "Uživatelský obsah"
    assert read_state(logger.paths.run_dir)["batch_records"]["batch_saved"]["status"] == status
    assert (Path(logger.paths.responses_dir) / "batch_saved_error_file_id.jsonl").read_bytes() == raw
    assert any(row["role"] == "batch_error" for row in logger.bundle.artifacts())
    assert not any(row["checkpoint_type"] == "files_downloaded_validated" for row in logger.bundle.checkpoints())
    _no_submit(client)


def _response_row(cid="one", model="gpt-5.2"):
    return {
        "custom_id": cid, "method": "POST", "url": "/v1/responses",
        "body": {"model": model, "input": "test", "text": text_format()},
    }


@pytest.mark.parametrize("fault", ["endpoint", "window", "empty", "method", "url", "models"])
def test_submit_rejects_invalid_envelope_before_transport(fault):
    client = Mock()
    rows = [_response_row()]
    options = {}
    if fault == "endpoint":
        options["endpoint"] = IMAGE_EDIT_ENDPOINT
    elif fault == "window":
        options["completion_window"] = "1h"
    elif fault == "empty":
        rows = []
    elif fault == "method":
        rows[0]["method"] = "GET"
    elif fault == "url":
        rows[0]["url"] = "/v1/chat/completions"
    else:
        rows.append(_response_row("two", "gpt-4.1"))
    with pytest.raises(ValueError):
        submit_verified_batch(client, "file_input", rows, **options)
    _no_submit(client)


@pytest.mark.parametrize("raw", [None, {}, {"unexpected": True}])
def test_recovered_work_order_must_be_complete_and_typed(raw):
    with pytest.raises(ContractError, match="WORK_ORDER_V2"):
        _work_orders({"work_orders": {"one": raw}, "requests": [{"custom_id": "one"}]})


def test_recovered_work_order_map_must_cover_requests():
    with pytest.raises(ContractError, match="mapování"):
        _work_orders({"work_orders": {}, "requests": [{"custom_id": "missing"}]})


@pytest.mark.parametrize("fault", ["rejected", "missing_id"])
def test_file_submit_persists_rejection_or_unknown_without_retry(tmp_path, fault):
    worker = make_worker(tmp_path, "GENERATE")
    client = Mock()
    client.upload_file.return_value = {"id": "file_input"}
    if fault == "rejected":
        client.create_batch.side_effect = OpenAIError("Odmítnuto", 400)
        expected = "not_submitted"
    else:
        client.create_batch.return_value = {"status": "validating"}
        expected = "submission_unknown"
    with pytest.raises((OpenAIError, ContractError)):
        worker._submit_generate_batch(client, manifest())
    state = read_state(worker.log.paths.run_dir)
    assert state["submission_unknown"] is (fault == "missing_id")
    assert state["batch_manifest_v4"]["state"] == (
        "failed" if fault == "rejected" else "submission_unknown"
    )
    repo = OrchestrationRepository(Path(worker.settings.log_dir) / "orchestration.sqlite3")
    with repo.connect() as db:
        assert {
            row[0]
            for row in db.execute("SELECT state FROM provider_operations")
        } == {expected}
    client.create_batch.assert_called_once()
    client.upload_file.assert_called_once()
    if fault == "missing_id":
        with pytest.raises(ContractError, match="Neurčitý|neurčitý"):
            worker._submit_generate_batch(client, manifest())
        client.create_batch.assert_called_once()
        client.upload_file.assert_called_once()


@pytest.fixture
def photo_job(tmp_path):
    source = tmp_path / "room.png"
    Image.new("RGB", (8, 6), (120, 130, 140)).save(source)
    return new_job(
        source_paths=[str(source)], human_prompt="Zachovat geometrii.", professional_prompt="",
        final_prompt="Zachovat geometrii.", prompt_source="manual", template_id="",
        prompt_model="", prompt_response_id="", image_model=image_edit_model_ids()[0],
        quality="high", size="auto", output_format="png", output_dir=str(tmp_path / "out"),
    )


@pytest.mark.parametrize("fault", ["not_submitted", "no_match", "ambiguous", "missing_id"])
def test_photo_recovery_requires_unique_exact_provider_identity(tmp_path, photo_job, fault):
    job = photo_job
    job.status = "submission_unknown" if fault != "not_submitted" else "preparing"
    job.input_file_id = "file_input"
    from test_photo_studio import _prepare_import_job
    _prepare_import_job(job, tmp_path / "LOG")
    root = save_job(job, tmp_path / "LOG")
    before = (root / "photo_job.json").read_bytes()
    match = {"id": "batch_found", "input_file_id": "file_input", "endpoint": IMAGE_EDIT_ENDPOINT, "status": "in_progress"}
    records = [
        {**match, "input_file_id": "file_other"},
        {**match, "endpoint": "/v1/responses"},
    ]
    if fault == "ambiguous":
        records += [match, {**match, "id": "batch_second"}]
    elif fault == "missing_id":
        records.append({key: value for key, value in match.items() if key != "id"})
    client = Mock()
    client.list_batches.return_value = records
    with pytest.raises(ValueError):
        refresh_job(client, job, tmp_path / "LOG")
    assert job.batch_id == ""
    assert (root / "photo_job.json").read_bytes() == before
    client.retrieve_batch.assert_not_called()
    client._req.assert_not_called()
    _no_submit(client)


@pytest.mark.parametrize("fault", ["rejected", "missing_id"])
def test_photo_submit_retains_durable_failure_classification(tmp_path, photo_job, fault):
    client = Mock()
    client.upload_file.side_effect = [{"id": "file_source"}, {"id": "file_input"}]
    if fault == "rejected":
        client.create_image_batch.side_effect = OpenAIError("Odmítnuto", 400)
    else:
        client.create_image_batch.return_value = {"status": "validating"}
    log_dir = tmp_path / "LOG"
    with pytest.raises((OpenAIError, ValueError)):
        prepare_and_submit(client, photo_job, log_dir)
    expected = "failed" if fault == "rejected" else "submission_unknown"
    saved = json.loads((log_dir / "PHOTO" / photo_job.job_id / "photo_job.json").read_text("utf-8"))
    assert saved["status"] == photo_job.status == expected
    assert saved["batch_id"] == ""
    repo = OrchestrationRepository(log_dir / "orchestration.sqlite3")
    with repo.connect() as db:
        assert db.execute(
            "SELECT state FROM provider_operations"
        ).fetchall() == [
            ("not_submitted" if fault == "rejected" else "submission_unknown",),
        ]
    client.create_image_batch.assert_called_once()
    client._req.assert_not_called()


@pytest.mark.parametrize("raw,message", [(b"\xff", "UTF-8"), (b"[]\n", "JSON objekt"), (b"{", "neplatný JSON")])
def test_photo_malformed_download_is_archived_without_output(tmp_path, photo_job, raw, message):
    photo_job.batch_id = "batch_photo"
    from test_photo_studio import _prepare_import_job
    _prepare_import_job(photo_job, tmp_path / "LOG")
    client = Mock()
    client.retrieve_batch.return_value = {
        "id": "batch_photo", "status": "completed", "output_file_id": "file_result",
        "input_file_id": photo_job.input_file_id, "endpoint": IMAGE_EDIT_ENDPOINT,
    }
    client.file_content.return_value = raw
    with pytest.raises(ValueError, match=message):
        download_results(client, photo_job, tmp_path / "LOG")
    assert (tmp_path / "LOG" / "PHOTO" / photo_job.job_id / "batch_output.jsonl").read_bytes() == raw
    assert not list(Path(photo_job.output_dir).iterdir())
    assert photo_job.items[0].technical_validation != "passed"
    _no_submit(client)
