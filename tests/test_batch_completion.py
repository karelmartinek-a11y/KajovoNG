"""Převzetí BATCH bez opakování generování a bez ztráty místních změn."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from kajovo.core.batch_completion import (
    complete_saved_batch, pending_batch_ids, read_state, local_batches, import_bundle,
)
from kajovo.core.config import AppSettings
from kajovo.core.contracts import ContractError
from test_generate_batch import manifest, outputs, raw


def saved_run(tmp_path, generate=False):
    run_dir = tmp_path / "LOG" / "RUN_110920261200_abcd"
    run_dir.mkdir(parents=True)
    state = {"run_id": run_dir.name, "project": "Můj projekt", "batch_id": "batch_work",
             "status": "batch_pending", "out_dir": str(tmp_path / "out")}
    if generate:
        state["generate_batch"] = manifest()
    save_state(run_dir, state)
    return run_dir, state


def save_state(run_dir, state):
    (run_dir / "run_state.json").write_text(json.dumps(state), encoding="utf-8")


def modify_output(run_dir, content="obsah"):
    return raw([{"custom_id": run_dir.name + "_C1", "response": {"status_code": 200,
                 "body": {"status": "completed", "output_text": json.dumps({
                     "contract": "C_FILES_ALL", "root": "projekt",
                     "files": [{"path": "hello.txt", "content": content}],
                 })}}}])


@pytest.mark.parametrize("generate", [False, True])
def test_completion_survives_restart_without_paid_calls(tmp_path, generate):
    run_dir, state = saved_run(tmp_path, generate)
    client = Mock()
    client.retrieve_batch.return_value = {"id": "batch_work", "status": "completed",
                                           "created_at": 123, "output_file_id": "file_out"}
    client.file_content.return_value = raw(outputs(state["generate_batch"])) if generate else modify_output(run_dir)
    result = complete_saved_batch(client, run_dir, "batch_work", AppSettings())
    assert result["status"] == "files_complete_unverified"
    restored = read_state(run_dir)
    assert pending_batch_ids(restored) == []
    assert restored["batch_records"]["batch_work"]["created_at"] == 123
    again = complete_saved_batch(client, run_dir, "batch_work", AppSettings())
    assert again["status"] == "files_complete_unverified"
    target = Path(state["out_dir"]) / ("maths.py" if generate else "projekt/hello.txt")
    target.write_text("ruční změna", encoding="utf-8")
    failed = complete_saved_batch(client, run_dir, "batch_work", AppSettings())
    assert failed["status"] == "partial"
    assert target.read_text(encoding="utf-8") == "ruční změna"
    assert pending_batch_ids(read_state(run_dir)) == ["batch_work"]
    client.create_response.assert_not_called()
    client.create_batch.assert_not_called()
    client.upload_file.assert_not_called()


@pytest.mark.parametrize("status", ["validating", "in_progress", "finalizing", "cancelling"])
def test_running_batch_is_normal_wait_without_writes(tmp_path, status):
    run_dir, state = saved_run(tmp_path, True)
    before = (run_dir / "run_state.json").read_bytes()
    client = Mock()
    client.retrieve_batch.return_value = {"status": status}
    result = complete_saved_batch(client, run_dir, "batch_work", AppSettings())
    assert result["status"] == "batch_pending"
    assert "později" in result["detail"]
    assert (run_dir / "run_state.json").read_bytes() == before
    assert not Path(state["out_dir"]).exists()
    client.file_content.assert_not_called()


@pytest.mark.parametrize("generate", [False, True])
@pytest.mark.parametrize("status", ["completed", "failed", "expired", "cancelled"])
def test_terminal_batch_without_files_is_partial(tmp_path, generate, status):
    run_dir, _ = saved_run(tmp_path, generate)
    client = Mock()
    client.retrieve_batch.return_value = {"status": status}
    result = complete_saved_batch(client, run_dir, "batch_work", AppSettings())
    assert result["status"] == "partial"
    assert pending_batch_ids(read_state(run_dir)) == ["batch_work"]


def test_completion_preserves_state_when_download_fails(tmp_path):
    run_dir, _ = saved_run(tmp_path)
    before = (run_dir / "run_state.json").read_bytes()
    client = Mock()
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "expired_file"}
    client.file_content.side_effect = RuntimeError("Výstup již není dostupný")
    with pytest.raises(RuntimeError):
        complete_saved_batch(client, run_dir, "batch_work", AppSettings())
    assert (run_dir / "run_state.json").read_bytes() == before
    with pytest.raises(ContractError):
        complete_saved_batch(client, run_dir, "batch_other", AppSettings())


def test_generate_repair_remains_pending_after_original_import(tmp_path):
    run_dir, state = saved_run(tmp_path, True)
    state["generate_batches"] = {"batch_repair": manifest()}
    save_state(run_dir, state)
    client = Mock()
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_out"}
    client.file_content.return_value = raw(outputs(manifest()))
    result = complete_saved_batch(client, run_dir, "batch_work", AppSettings())
    assert result["status"] == "batch_pending"
    assert pending_batch_ids(read_state(run_dir)) == ["batch_repair"]
    complete_saved_batch(client, run_dir, "batch_repair", AppSettings())
    assert pending_batch_ids(read_state(run_dir)) == []
    assert local_batches(tmp_path / "LOG")["batch_repair"]["run_id"] == run_dir.name


@pytest.mark.parametrize("bad", [b"[]", b"invalid", b"", b"null"])
def test_malformed_bundle_never_reports_success(tmp_path, bad):
    result = import_bundle(bad, str(tmp_path))
    assert result["status"] == "partial"
    assert not result["written"]


def test_modify_rejects_duplicate_and_unrelated_ids_before_writing(tmp_path):
    run_dir, state = saved_run(tmp_path)
    valid = json.loads(modify_output(run_dir))
    for rows in ([valid, valid], [dict(valid, custom_id="other")]):
        client = Mock()
        client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_out"}
        client.file_content.return_value = raw(rows)
        with pytest.raises(ContractError):
            complete_saved_batch(client, run_dir, "batch_work", AppSettings())
        assert not Path(state["out_dir"]).exists()


def test_corrupt_history_record_is_skipped(tmp_path):
    run_dir, _ = saved_run(tmp_path)
    save_state(run_dir, {"batch_id": "batch_work", "generate_batches": []})
    assert local_batches(tmp_path / "LOG") == {}
