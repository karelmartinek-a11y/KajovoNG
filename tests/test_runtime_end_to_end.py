"""Celé offline životní cesty s novým procesem a skutečnými soubory."""

import base64
from native_provider_fixtures import native_fixture
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def child(code, root):
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", KAJOVO_LIVE_ACCEPTANCE="0")
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests")])
    result = subprocess.run(
        [sys.executable, "-c", code, str(root)], cwd=root, env=environment,
        capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class PhotoTransport:
    """Fake HTTP hranice; skutečný OpenAIClient i backend zůstávají aktivní."""

    def __init__(self, root, completed):
        self.root = root
        self.completed = completed
        self.calls = []
        self.uploads = 0

    def request(self, method, url, **kwargs):
        from test_photo_studio import _image_bytes
        path = url.split("/v1", 1)[1]
        self.calls.append((method, path))
        batch = {
            "id": "batch_photo", "input_file_id": "file_batch", "endpoint": "/v1/images/edits",
            "status": "completed" if self.completed else "validating",
            "output_file_id": "file_output" if self.completed else None, "error_file_id": None,
            "request_counts": {"total": 1, "completed": int(self.completed), "failed": 0},
        }
        if (method, path) == ("POST", "/files"):
            self.uploads += 1
            purpose = kwargs["data"]["purpose"]
            uploaded = kwargs["files"]["file"][1].read()
            if purpose == "batch":
                row = json.loads(uploaded)
                assert row["url"] == "/v1/images/edits" and row["method"] == "POST"
                assert row["body"]["images"] == [{"file_id": "file_source"}]
                (self.root / "custom_id.txt").write_text(row["custom_id"])
                value = {"id": "file_batch"}
            else:
                assert purpose == "user_data" and uploaded == _image_bytes()
                value = {"id": "file_source"}
        elif (method, path) == ("POST", "/batches"):
            body = kwargs["json"]
            assert body["input_file_id"] == "file_batch"
            assert body["endpoint"] == "/v1/images/edits" and body["completion_window"] == "24h"
            assert not self.completed
            value = batch
        elif (method, path) == ("GET", "/batches/batch_photo"):
            value = batch
        elif (method, path) == ("GET", "/batches"):
            value = {"data": [batch], "has_more": False}
        elif (method, path) == ("GET", "/files/file_output/content"):
            value = {"custom_id": (self.root / "custom_id.txt").read_text(), "error": None,
                     "response": {"status_code": 200, "body": {"data": [{
                         "b64_json": base64.b64encode(_image_bytes()).decode("ascii"),
                     }]}}}
            raw = (json.dumps(value) + "\n").encode()
            return SimpleNamespace(status_code=200, headers={"content-type": "application/jsonl"},
                                   content=raw, text=raw.decode(), json=lambda: value)
        else:
            raise AssertionError(f"Neočekávaná síťová operace: {method} {path}")
        raw = json.dumps(native_fixture(method, path, value, kwargs.get("json") or kwargs.get("data"))).encode()
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"},
                               content=raw, text=raw.decode(), json=lambda: value)


def photo_stage(root, completed):
    from kajovo.core import photo_batch
    from kajovo.core.openai_client import OpenAIClient
    from test_photo_studio import _frozen_job, _image_bytes
    client = OpenAIClient("offline-test-key")
    transport = PhotoTransport(root, completed)
    client.session.request = transport.request
    log = root / "LOG"
    if not completed:
        source, job = _frozen_job(root)
        original = source.read_bytes()
        photo_batch.prepare_and_submit(client, job, log)
        assert job.status == "validating" and job.batch_id == "batch_photo"
        assert source.read_bytes() == original
        assert transport.calls.count(("POST", "/batches")) == 1
        assert transport.uploads == 2
    else:
        jobs = photo_batch.load_jobs(log)
        assert len(jobs) == 1 and jobs[0].status == "validating"
        job = jobs[0]
        photo_batch.refresh_job(client, job, log)
        assert job.status == "completed" and job.output_file_id == "file_output"
        photo_batch.download_results(client, job, log)
        assert job.status == "downloaded"
        assert Path(job.items[0].output_path).read_bytes() == _image_bytes()
        assert (root / "input.png").read_bytes() == _image_bytes()
        assert not any(method == "POST" for method, path in transport.calls)
        assert transport.uploads == 0
        assert photo_batch.load_jobs(log)[0].status == "downloaded"
    (root / ("photo_finish.json" if completed else "photo_start.json")).write_text(json.dumps(transport.calls))


def test_photo_http_submit_restart_download_byte_to_disk(tmp_path):
    child("from pathlib import Path; import sys; from test_runtime_end_to_end import photo_stage; "
          "photo_stage(Path(sys.argv[1]), False)", tmp_path)
    child("from pathlib import Path; import sys; from test_runtime_end_to_end import photo_stage; "
          "photo_stage(Path(sys.argv[1]), True)", tmp_path)


def delivery_stage(root, mode, batch, finish):
    from change_v2_fixtures import scenario, run, default_files, batch_output_rows, raw_jsonl
    from kajovo.core.generate_batch import process_saved_batch
    from kajovo.core.orchestration.publish import publish_staged_run
    from kajovo.core.config import AppSettings
    from unittest.mock import Mock
    info = root / "delivery.json"
    if not finish:
        files = default_files(mode)
        if mode == "MODIFY":
            files[0]["action"] = "modify"
        worker, client, responder = scenario(root, mode, batch=batch, files=files)
        results, errors = run(worker, client)
        assert not errors, errors
        assert results[0]["status"] == ("batch_pending" if batch else "files_complete_unverified")
        target = Path(worker.cfg.out_dir) / "hello.txt"
        assert not target.exists()
        info.write_text(json.dumps({"run": str(worker.log.paths.run_dir), "target": str(target)}))
    else:
        data = json.loads(info.read_text())
        run_dir = Path(data["run"])
        if batch:
            state = json.loads((run_dir / "run_state.json").read_text())
            client = Mock()
            client.file_content.return_value = raw_jsonl(batch_output_rows(state["generate_batch"]))
            result = process_saved_batch(client, run_dir, "batch_work", AppSettings(), batch={
                "id": "batch_work", "input_file_id": state["batch_input_file_id"],
                "endpoint": "/v1/responses", "status": "completed", "output_file_id": "file_results",
            })
            assert result["status"] == "files_complete_unverified"
            client.create_batch.assert_not_called()
            client.create_response.assert_not_called()
        report = publish_staged_run(run_dir)
        assert report["status"] == "committed"
        assert Path(data["target"]).read_text() == "content:hello.txt\n"
        from kajovo.core.run_bundle import LegacyRunAdapter
        adapter = LegacyRunAdapter(run_dir)
        assert adapter.run_record()["status"] == "completed_unverified"
        assert adapter.bundle.verify_integrity()["valid"]


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
def test_delivery_restart_and_explicit_take_to_final_file(tmp_path, mode, batch):
    for finish in (False, True):
        child("from pathlib import Path; import sys; from test_runtime_end_to_end import delivery_stage; "
              f"delivery_stage(Path(sys.argv[1]), {mode!r}, {batch!r}, {finish!r})", tmp_path)


def response_stage(root, finish):
    from kajovo.core.response_journal import ResponseJournal, ResponsePending
    from kajovo.core.runlog import RunLogger
    from kajovo.core.openai_client import OpenAIError
    from test_response_journal import Clock, execute
    from unittest.mock import Mock
    clock = Clock()
    logger = RunLogger(str(root / "LOG"), "RUN_130920260100_TEST", resume=finish)
    journal = ResponseJournal(logger, clock=clock.now, sleep=clock.sleep)
    client = Mock()
    if not finish:
        logger.update_state({"mode": "QA", "response_transport": "background", "status": "response_pending"})
        client.create_response.return_value = {"id": "resp_restart", "status": "queued"}
        client.retrieve_response.side_effect = OpenAIError("Read timeout")
        with pytest.raises(ResponsePending):
            execute(journal, client)
        assert client.create_response.call_count == 1
    else:
        client.retrieve_response.return_value = {"id": "resp_restart", "status": "completed", "output": []}
        assert execute(journal, client)["id"] == "resp_restart"
        client.create_response.assert_not_called()
        client.retrieve_response.assert_called_once_with("resp_restart")


def test_response_pending_resume_in_new_process_without_post(tmp_path):
    for finish in (False, True):
        child("from pathlib import Path; import sys; from test_runtime_end_to_end import response_stage; "
              f"response_stage(Path(sys.argv[1]), {finish!r})", tmp_path)
