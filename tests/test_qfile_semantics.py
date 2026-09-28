"""Sémantické hranice QFILE bez živé sítě."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from change_v2_fixtures import run
from kajovo.core.context_compiler import content_hash
from test_workflows import make_worker, response


def _client():
    client = Mock()
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000,
        "request_hash": content_hash(payload),
    }
    client.retrieve_file.return_value = {
        "id": "file_test",
        "filename": "input.txt",
        "bytes": 100,
    }
    client.upload_file.return_value = {"id": "file_test"}
    return client


def _plan(path="navrh.md"):
    return {
        "result": {
            "status": "ready",
            "data": {
                "proposed_path": path,
                "format": "md",
                "purpose": "Dodat jeden Markdown soubor.",
                "allow_empty": False,
                "acceptance": [],
            },
        }
    }


def test_qfile_plan_requires_separate_confirmation_before_file_content(tmp_path):
    worker = make_worker(tmp_path, "QFILE")
    worker.cfg.qfile_suggest_path = True
    worker.cfg.qfile_output_path = ""
    client = _client()
    client.create_response.return_value = response(0, _plan())

    results, errors = run(worker, client)

    assert errors == []
    assert results[0]["status"] == "qfile_plan_ready"
    assert results[0]["requires_user_confirmation"] is True
    assert results[0]["qfile_plan"]["proposed_path"] == "navrh.md"
    assert client.create_response.call_count == 1
    payload = client.create_response.call_args.args[0]
    assert payload["text"]["format"]["name"] == "QFILE_PLAN_V1"
    assert not (Path(worker.cfg.out_dir) / "navrh.md").exists()
    client.create_batch.assert_not_called()


def test_qfile_confirmed_plan_produces_only_approved_file_contract(tmp_path):
    worker = make_worker(tmp_path, "QFILE")
    worker.cfg.qfile_output_path = "navrh.md"
    worker.cfg.qfile_output_format = "md"
    worker.cfg.qfile_suggest_path = False
    worker.cfg.qfile_plan = _plan()["result"]["data"]
    client = _client()
    client.create_response.return_value = response(0, {"content": "Hotovo.\n"})

    results, errors = run(worker, client)

    assert errors == []
    assert results[0]["status"] == "files_complete_unverified"
    assert results[0]["qfile_plan"]["proposed_path"] == "navrh.md"
    payload = client.create_response.call_args.args[0]
    assert payload["text"]["format"]["name"] == "FILE_CONTENT_V1"
    assert not (Path(worker.cfg.out_dir) / "navrh.md").exists()
    staged = results[0]["saved"]["staged"]
    assert len(staged) == 1
    assert staged[0]["path"] == "navrh.md"
    assert staged[0]["sha256"]
    client.create_batch.assert_not_called()


def test_qfile_rejects_unsafe_suggested_path_before_any_file_delivery(tmp_path):
    worker = make_worker(tmp_path, "QFILE")
    worker.cfg.qfile_suggest_path = True
    worker.cfg.qfile_output_path = ""
    client = _client()
    client.create_response.return_value = response(0, _plan("../escape.md"))

    results, errors = run(worker, client)

    assert results == []
    assert errors
    assert client.create_response.call_count == 1
    payload = client.create_response.call_args.args[0]
    assert payload["text"]["format"]["name"] == "QFILE_PLAN_V1"
    assert "FILE_CONTENT_V1" not in str(payload)
    assert not (tmp_path / "escape.md").exists()
    client.create_batch.assert_not_called()
