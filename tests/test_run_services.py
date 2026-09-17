"""Přílohy a diagnostika zachovají unknown i dohledatelné lokální chyby."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kajovo.core.openai_transport import SubmissionOutcomeUnknown
from test_workflows import make_worker


def test_unknown_upload_stops_run_and_blocks_same_run_restart(tmp_path):
    worker = make_worker(tmp_path, "QA")
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "data.txt").write_text("vstup", encoding="utf-8")
    worker.cfg.in_dir = str(folder)
    client = Mock()
    client.upload_file.side_effect = SubmissionOutcomeUnknown("upload_file", "POST", "/files")
    results = []
    worker.finished_ok.connect(results.append)
    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client):
        worker.execute()
        state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
        assert state["status"] == "submission_unknown"
        assert state["unknown_submission"]["operation"] == "upload_file"
        assert worker.log.bundle.verify_integrity()["status"] != "changed"
        worker.execute()
    assert client.upload_file.call_count == 1
    client.create_response.assert_not_called()
    assert not results


def test_diagnostics_archive_records_unreadable_files(tmp_path):
    worker = make_worker(tmp_path, "QA")
    worker.log = Mock(paths=worker.log.paths)
    text = tmp_path / "data.txt"
    text.write_text("diagnostika", encoding="utf-8")
    binary = tmp_path / "data.bin"
    binary.write_bytes(b"\x00\x01")
    missing = str(tmp_path / "missing.txt")
    assert worker._build_diag_text([str(text), str(binary), missing]) == "# data.txt\ndiagnostika"
    archive = worker._write_diagnostics_json(str(tmp_path), [str(text), str(binary), missing])
    data = json.loads(Path(archive).read_text(encoding="utf-8"))
    assert data["file_count"] == 2
    assert data["files"][1]["encoding"] == "base64"
    assert worker.log.exception.call_count >= 2
    assert worker._write_diagnostics_json(str(tmp_path / "absent"), []) is None


@pytest.mark.parametrize("remote", [False, True])
def test_diagnostics_collection_and_upload(tmp_path, remote):
    worker = make_worker(tmp_path, "QA")
    worker.cfg.diag_windows_in = not remote
    worker.cfg.diag_ssh_in = remote
    worker.cfg.ssh_host = "offline.invalid"
    worker.cfg.ssh_user = "test"
    worker.log = Mock(paths=worker.log.paths)
    text = tmp_path / "data.txt"
    text.write_text("diagnostika", encoding="utf-8")
    client = Mock()
    client.upload_file.return_value = {"id": "file_diag"}
    target = "kajovo.core.diagnostics.ssh.collect_ssh_diagnostics" if remote else "kajovo.core.diagnostics.windows.collect_windows_diagnostics"
    with patch(target, return_value=(str(tmp_path), [str(text)])):
        ids, content = worker._maybe_collect_diagnostics(client)
    assert ids == ["file_diag"] and "diagnostika" in content
    client.upload_file.assert_called_once()
    assert worker.log.update_state.call_args.args[0]["diagnostics_delivery"]["delivered"]


def test_unknown_indexation_never_falls_back_to_new_work(tmp_path):
    worker = make_worker(tmp_path, "QA")
    worker.cfg.in_dir = str(tmp_path)
    worker.cfg.model_caps["supports_vector_store"] = True
    worker._zip_in_dir = Mock(return_value=str(tmp_path / "input.txt"))
    (tmp_path / "input.txt").write_text("data", encoding="utf-8")
    client = Mock()
    client.upload_file.return_value = {"id": "file_input"}
    client.create_vector_store.side_effect = SubmissionOutcomeUnknown("create_vector_store", "POST", "/vector_stores")
    with pytest.raises(SubmissionOutcomeUnknown):
        worker._prepare_in_dir_upload(client)
    client.create_vector_store.assert_called_once()
    client.create_response.assert_not_called()


@pytest.mark.parametrize("remote", [False, True])
def test_diagnostics_failure_is_recorded(tmp_path, remote):
    worker = make_worker(tmp_path, "QA")
    worker.cfg.diag_windows_in = not remote
    worker.cfg.diag_ssh_in = remote
    worker.cfg.ssh_host = "offline.invalid"
    worker.cfg.ssh_user = "test"
    worker.log = Mock(paths=worker.log.paths)
    target = "kajovo.core.diagnostics.ssh.collect_ssh_diagnostics" if remote else "kajovo.core.diagnostics.windows.collect_windows_diagnostics"
    with patch(target, side_effect=OSError("offline failure")), pytest.raises(RuntimeError):
        worker._maybe_collect_diagnostics(Mock())
    worker.log.exception.assert_called_once()
