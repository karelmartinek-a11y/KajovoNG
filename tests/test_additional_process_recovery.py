"""Nové procesy pro Responses, Comic a Cascade; hranice důkazů jsou explicitní."""

import base64
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
import requests

from test_runtime_end_to_end import ROOT, child


def response_process(root, finish):
    from kajovo.core.openai_client import OpenAIClient
    from kajovo.core.response_journal import ResponseJournal, ResponsePending
    from kajovo.core.runlog import RunLogger
    from test_response_journal import Clock, execute

    logger = RunLogger(str(root / "LOG"), "RUN_HTTP_RESPONSE", resume=finish)
    clock = Clock()
    journal = ResponseJournal(logger, clock=clock.now, sleep=clock.sleep)
    client = OpenAIClient("offline", base_url="https://offline.invalid/v1")
    client._sdk = None
    calls = []

    def request(method, url, **kwargs):
        path = url.split("/v1", 1)[1]
        calls.append({"method": method, "path": path, "payload": kwargs.get("json")})
        if (method, path) == ("GET", "/models"):
            response = requests.Response()
            response.status_code = 200
            response.headers["Content-Type"] = "application/json"
            response._content = b'{"data":[{"id":"gpt-5.4"}]}'
            return response
        if method == "POST":
            assert not finish and path == "/responses"
            assert kwargs["json"] == {"model": "gpt-5.4", "input": "test", "background": True, "store": True,
                                      "text": {"format": {"type": "json_schema", "name": "TEXT_RESPONSE", "strict": True,
                                                          "schema": {"type": "object", "properties": {"text": {"type": "string"}},
                                                                     "required": ["text"], "additionalProperties": False}}}}
            status = "queued"
        else:
            assert path == "/responses/resp_http_restart"
            status = "completed" if finish else "in_progress"
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({"id": "resp_http_restart", "status": status, "output": []}).encode()
        return response

    client.session.request = request
    if finish:
        assert execute(journal, client)["status"] == "completed"
        assert calls == [{"method": "GET", "path": "/responses/resp_http_restart", "payload": None}]
    else:
        logger.update_state({"mode": "QA", "response_transport": "background", "status": "response_pending"})
        journal.timeout_s = 1
        with pytest.raises(ResponsePending):
            execute(journal, client)
        assert sum(row["method"] == "POST" for row in calls) == 1
    (root / f"responses-{finish}.json").write_text(json.dumps(calls), encoding="utf-8")


def test_responses_exact_http_pending_recovery_in_new_process(tmp_path):
    for finish in (False, True):
        child("from pathlib import Path; import sys; from test_additional_process_recovery import response_process; " +
              f"response_process(Path(sys.argv[1]), {finish!r})", tmp_path)


class ComicHttp:
    """Řízený provider vrací HTTP bytes; doménovou persistenci zpracovává ComicService."""

    def __init__(self, root, finish):
        from test_comic_domain import WorkingClient
        self.root, self.finish = root, finish
        self.provider = WorkingClient()
        self.path = root / "comic-provider.json"
        if self.path.exists():
            state = json.loads(self.path.read_text())
            for name, value in state.items():
                if name == "files":
                    value = {key: base64.b64decode(binary) for key, binary in value.items()}
                elif name == "fail_ids":
                    value = set(value)
                setattr(self.provider, name, value)
        self.calls = []

    def request(self, method, url, **kwargs):
        path = url.split("/v1", 1)[1]
        body = kwargs.get("json")
        record = {"method": method, "path": path, "payload": body}
        self.calls.append(record)
        if path == "/models":
            value = {"data": self.provider.list_models(), "has_more": False}
        elif path == "/responses/input_tokens":
            value = self.provider.count_input_tokens(body)
        elif path == "/responses":
            assert method == "POST" and body["background"] is True and body["text"]["format"]["strict"] is True
            value = self.provider.create_response(body)
        elif path == "/files" and method == "POST":
            raw = kwargs["files"]["file"][1].read()
            identifier = "file_" + str(len(self.provider.files))
            self.provider.files[identifier] = raw
            if kwargs["data"]["purpose"] == "batch":
                rows = [json.loads(line) for line in raw.splitlines()]
                record["jsonl"] = rows
                assert len(rows) == 3 and len({row["custom_id"] for row in rows}) == 3
                assert all(row["method"] == "POST" and row["url"] == "/v1/images/generations" for row in rows)
                assert all(row["body"]["n"] == 1 for row in rows)
            value = {"id": identifier}
        elif path == "/batches" and method == "POST":
            assert not self.finish
            assert body["completion_window"] == "24h" and body["endpoint"] == "/v1/images/generations"
            rows = [json.loads(line) for line in self.provider.files[body["input_file_id"]].splitlines()]
            value = self.provider.create_image_batch(body["input_file_id"], rows)
        elif path.startswith("/batches/") and method == "GET":
            identifier = path.rsplit("/", 1)[1]
            if self.finish and self.provider.batches[identifier]["status"] != "completed":
                self.provider.fail_ids.add(self.provider.batch_rows[identifier][1]["custom_id"])
                self.provider.complete(identifier)
                self.provider.batches[identifier]["request_counts"] = {"total": 3, "completed": 2, "failed": 1}
            value = self.provider.retrieve_batch(identifier)
        elif path.startswith("/files/") and path.endswith("/content"):
            raw = self.provider.file_content(path.split("/")[2])
            value = None
        else:
            raise AssertionError(f"Neočekávané HTTP: {method} {path}")
        state = {name: getattr(self.provider, name) for name in ("files", "batches", "batch_rows", "fail_ids", "submits", "responses", "image_calls")}
        state["files"] = {key: base64.b64encode(binary).decode() for key, binary in state["files"].items()}
        state["fail_ids"] = list(state["fail_ids"])
        self.path.write_text(json.dumps(state), encoding="utf-8")
        with (self.root / "comic-http.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/octet-stream" if value is None else "application/json"
        response._content = raw if value is None else json.dumps(value).encode()
        return response


def comic_process(root, finish):
    from kajovo.core.comic_service import ComicService
    from kajovo.core.comic_store import ComicStore
    from kajovo.core.config import AppSettings
    from kajovo.core.openai_client import OpenAIClient
    from test_comic_domain import make_panel
    from PIL import Image

    transport = ComicHttp(root, finish)
    client = OpenAIClient("offline", base_url="https://offline.invalid/v1")
    client._sdk = None
    client.session.request = transport.request
    settings = AppSettings(comic_library_dir=str(root / "comics"), log_dir=str(root / "LOG"))
    service = ComicService(settings, client)
    info = root / "comic-operation.txt"
    if not finish:
        project = service.store.project("Restart test")
        service.run(service.start_bible(project))
        panels = [make_panel(service, project) for _ in range(3)]
        operation = service.start_panels(project, panels)
        assert service.run(operation)["status"] == "batch_pending"
        info.write_text(operation)
        assert transport.provider.submits == 1
    else:
        operation = info.read_text()
        assert service.run(operation, allow_submit=False)["status"] == "partial"
        versions = ComicStore(settings.comic_library_dir).rows("panel_versions")
        assert len(versions) == 2
        from test_comic_domain import png
        store = ComicStore(settings.comic_library_dir)
        for row in versions:
            raw = store.asset_path(row["raw_asset_id"]).read_bytes()
            assert raw == png()
            asset = store.get("assets", row["asset_id"])
            output = store.asset_path(asset["id"])
            assert hashlib.sha256(output.read_bytes()).hexdigest() == asset["sha256"]
            with Image.open(output) as image:
                image.load()
                assert image.size == (1024, 1024)
        service.run(operation, allow_submit=False)
        assert len(service.store.rows("panel_versions")) == 2
        assert not any(row["method"] == "POST" for row in transport.calls)


def test_comic_http_partial_ingest_restart_is_idempotent_without_submit(tmp_path):
    for finish in (False, True, True):
        child("from pathlib import Path; import sys; from test_additional_process_recovery import comic_process; " +
              f"comic_process(Path(sys.argv[1]), {finish!r})", tmp_path)


def cascade_process(root, finish):
    from kajovo.core.cascade_types import CascadeDefinition
    from test_cascade_v2 import _client, _text_step, _worker
    from test_cascade_audit2 import observe

    path = root / "cascade.json"
    if not finish:
        definition = CascadeDefinition("Restart", steps=[_text_step("První")])
        path.write_text(json.dumps(definition.to_dict()))
    definition = CascadeDefinition.from_dict(json.loads(path.read_text()))
    worker = _worker(definition, root)
    client = _client()

    def interrupted(payload):
        (root / "cascade-request.json").write_text(json.dumps(payload))
        os._exit(77)

    client.create_response.side_effect = interrupted
    errors, _details, events, results = observe(worker)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()
    assert finish and errors and not results
    assert events[-1].state == "submission_unknown"
    client.create_response.assert_not_called()


def test_cascade_hard_exit_preserves_submission_unknown_in_new_process(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
    code = "from pathlib import Path; import sys; from test_additional_process_recovery import cascade_process; cascade_process(Path(sys.argv[1]), False)"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 77, result.stdout + result.stderr
    original = (tmp_path / "cascade-request.json").read_bytes()
    child("from pathlib import Path; import sys; from test_additional_process_recovery import cascade_process; cascade_process(Path(sys.argv[1]), True)", tmp_path)
    assert (tmp_path / "cascade-request.json").read_bytes() == original


def history_process(root, relation):
    from types import SimpleNamespace
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    from kajovo.core.config import AppSettings
    from kajovo.core.openai_client import OpenAIClient
    from kajovo.core.run_bundle import LegacyRunAdapter
    from kajovo.studio.history_launcher import HistoryBranchLauncher
    from kajovo.studio.operations import Operations
    from test_delivery_http_graph import RecordingHttp

    app = QApplication([])
    parent = QWidget()
    info = json.loads((root / "delivery-info.json").read_text())
    adapter = LegacyRunAdapter(Path(info["run"]))
    before = {str(path.relative_to(adapter.root)): path.read_bytes() for path in adapter.root.rglob("*") if path.is_file()}
    transport = RecordingHttp(root, "GENERATE")
    client = OpenAIClient("offline", base_url="https://offline.invalid/v1")
    client._sdk = None
    client._transport.session = transport
    settings = AppSettings(log_dir=str(root / "LOG"), cache_dir=str(root / "cache"))
    operations = Operations(parent)
    context = SimpleNamespace(settings=settings, operations=operations, api_key="offline", models=["gpt-4o-mini"])
    launcher = HistoryBranchLauncher(context)
    checkpoint = next(row for row in adapter.checkpoints() if row["safe_to_continue"] and row["checkpoint_type"] == "input_ready")
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], relation)
    received = []
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    operations.completed.connect(lambda *_: loop.quit() if received and received[0].terminal else None)
    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client):
        launcher.launch_async(adapter, preview, "Zachovat přesný obsah." if relation == "repair" else "", receive=received.append)
        timeout.start(30000)
        # Reálný GUI event loop obslouží přípravu, QThread a jeho signály.
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if received and received[0].terminal else None)
        timer.start(10)
        loop.exec()
        timer.stop()
    assert received and received[0].terminal == "files_complete_unverified", received[0].error if received else "timeout"
    record = received[0]
    assert record.result["status"] == "files_complete_unverified"
    target = Path(settings.log_dir) / record.identifier
    assert LegacyRunAdapter(target).bundle.verify_integrity()["valid"]
    assert before == {str(path.relative_to(adapter.root)): path.read_bytes() for path in adapter.root.rglob("*") if path.is_file()}
    assert any(row["method"] == "POST" and row["path"] == "/responses" for row in transport.calls)
    assert not operations.active
    for operation in operations.records.values():
        if operation.dialog:
            operation.dialog.close()
    parent.close()
    app.processEvents()


@pytest.mark.parametrize("relation", ["continue", "rerun", "repair"])
def test_history_actual_launcher_qthread_after_new_process(tmp_path, relation):
    child("from pathlib import Path; import sys; from test_delivery_http_graph import delivery_process; delivery_process(Path(sys.argv[1]), 'GENERATE', False, 'prepare')", tmp_path)
    child("from pathlib import Path; import sys; from test_additional_process_recovery import history_process; " +
          f"history_process(Path(sys.argv[1]), {relation!r})", tmp_path)
