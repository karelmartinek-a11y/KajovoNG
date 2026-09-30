"""QA/QFILE přes HTTP hranici, produkční Qt akci a restart procesu."""

import copy
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
import requests

from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.runlog import RunLogger
from kajovo.core.runs.executor import RunExecutor
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from kajovo.studio.workbench import Workbench
from test_runtime_end_to_end import ROOT, child
from test_workflows import make_worker


def qa_value(identifier="SRC-USER-TEXT"):
    return {"result": {"status": "ready", "data": {
        "answer": "Doložená odpověď.", "claims": [{"id": "C1", "text": "Tvrzení",
        "evidence_ids": [identifier] if identifier else [], "certainty": "supported"}],
        "limitations": ["Existence odkazu není důkaz pravdivosti."],
    }}}


def plan_value(path="výsledek/navrh.md"):
    return {"result": {"status": "ready", "data": {
        "proposed_path": path, "format": "md", "purpose": "Markdown výsledek",
        "allow_empty": False, "acceptance": [],
    }}}


class WorkflowHttp:
    def __init__(self, root, *, answer=None, fault=""):
        self.root, self.answer, self.fault = root, answer, fault
        self.calls = []

    def request(self, method, url, **kwargs):
        assert url.startswith("https://offline.invalid/v1/")
        path = url.split("/v1", 1)[1]
        body = copy.deepcopy(kwargs.get("json"))
        row = {"method": method, "path": path, "body": body}
        self.calls.append(row)
        with (self.root / "workflow-http.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        if (method, path) == ("GET", "/models"):
            value = {"data": [{"id": "gpt-4o-mini"}], "has_more": False}
        elif (method, path) == ("GET", "/responses/resp_parent"):
            value = {"id": "resp_parent", "model": "gpt-4o-mini", "status": "completed"}
        elif (method, path) == ("POST", "/responses/input_tokens"):
            value = {"input_tokens": 128}
        elif (method, path) == ("POST", "/responses"):
            assert body["model"] == "gpt-4o-mini"
            assert "background" not in body and "store" not in body
            assert body["text"]["format"]["strict"] is True
            name = body["text"]["format"]["name"]
            if self.fault == "after_dispatch":
                os._exit(77)
            if self.fault == "timeout":
                raise requests.Timeout("syntetický timeout po dispatchi")
            answer = self.answer
            if answer is None:
                answer = qa_value() if name == "QA_ANSWER_V2" else plan_value() if name == "QFILE_PLAN_V1" else {"content": "# Výsledek\nPřesné bytes.\n"}
            value = {"id": "resp_" + name.lower(), "model": body["model"], "status": "completed",
                     "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer, ensure_ascii=False)}]}],
                     "usage": {"input_tokens": 128, "output_tokens": 32}}
            if self.fault == "refusal":
                value["output"] = [{"type": "message", "content": [{"type": "refusal", "refusal": "Odmítnuto"}]}]
            if self.fault == "incomplete":
                value.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        else:
            raise AssertionError(f"Neočekávaná hranice: {method} {path}")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(value, ensure_ascii=False).encode()
        return response


def http_client(transport):
    client = OpenAIClient("synthetic-offline", base_url="https://offline.invalid/v1")
    client._sdk = None
    client.session.request = transport.request
    return client


def page_fixture(qtbot, monkeypatch, tmp_path, mode, transport):
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    operations = Operations(None)
    context = StudioContext(settings, operations, api_key="synthetic-offline", client_factory=lambda *a, **k: http_client(transport))
    context.models = ["gpt-4o-mini"]
    page = Workbench(context)
    operations.setParent(page)
    qtbot.addWidget(page)
    monkeypatch.setattr("kajovo.core.runs.executor.OpenAIClient", lambda *a, **k: http_client(transport))
    page.apply_state({"project": "HTTP ověření", "prompt": "Odpověz z tohoto zadání.", "mode": mode,
                      "model": "gpt-4o-mini", "out_dir": str(tmp_path / "OUT"),
                      "qfile_suggest_path": mode == "QFILE", "qfile_output_format": "md"})
    return page, operations


def settle(qtbot, operations, page):
    qtbot.waitUntil(lambda: not operations.active and not page.busy_outputs, timeout=30000)
    for record in operations.records.values():
        if record.dialog:
            qtbot.addWidget(record.dialog)
            record.dialog.close()


@pytest.mark.parametrize("continuity", [False, True])
def test_qa_actual_button_http_request_semantics_and_ui(qtbot, monkeypatch, tmp_path, continuity):
    transport = WorkflowHttp(tmp_path)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, "QA", transport)
    page.widgets["response_id"].setText("resp_parent")
    page.widgets["qa_continue_conversation"].setChecked(continuity)
    page.start_button.click()
    settle(qtbot, operations, page)
    posts = [row for row in transport.calls if row["path"] == "/responses"]
    assert len(posts) == 1, [(r.error, r.result) for r in operations.records.values()]
    payload = posts[0]["body"]
    assert payload.get("previous_response_id") == ("resp_parent" if continuity else None)
    assert "SRC-USER-TEXT" in json.dumps(payload["input"])
    result = page.result.value
    assert result["status"] == "completed" and result["claims"][0]["evidence_ids"] == ["SRC-USER-TEXT"]
    record = next(iter(operations.records.values()))
    assert record.terminal == "completed" and not record.worker.isRunning()
    manifests = list((tmp_path / "LOG" / record.identifier / "manifests").glob("*QA_answer*.json"))
    assert len(manifests) == 1 and json.loads(manifests[0].read_text())["answer"] == result["text"]
    assert page.widgets["qa_continue_conversation"].isChecked() is False


@pytest.mark.parametrize("answer,fault", [(qa_value("foreign"), ""), (qa_value(None), ""), ({"wrong": True}, ""), (None, "refusal"), (None, "incomplete"), (None, "timeout")])
def test_qa_invalid_output_never_reports_completed_or_retries(qtbot, monkeypatch, tmp_path, answer, fault):
    transport = WorkflowHttp(tmp_path, answer=answer, fault=fault)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, "QA", transport)
    page.start_button.click()
    settle(qtbot, operations, page)
    record = next(iter(operations.records.values()))
    assert record.terminal == ("submission_unknown" if fault == "timeout" else "failed")
    assert record.error and not record.result
    assert sum(row["path"] == "/responses" for row in transport.calls) == 1
    assert not list((tmp_path / "LOG").rglob("*QA_answer*.json"))


def test_qfile_button_plan_confirmation_staging_and_publish_bytes(qtbot, monkeypatch, tmp_path):
    from kajovo.core.orchestration.publish import publish_staged_run
    transport = WorkflowHttp(tmp_path)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, "QFILE", transport)
    page.start_button.click()
    settle(qtbot, operations, page)
    assert len([row for row in transport.calls if row["path"] == "/responses"]) == 1
    assert not (tmp_path / "OUT" / "výsledek/navrh.md").exists()
    assert "výslovně potvrdíte" in page.validation.text(), (page.saved_extras, [(r.error, r.result) for r in operations.records.values()])
    assert page.saved_extras["qfile_plan"] == plan_value()["result"]["data"]
    page.start_button.click()
    settle(qtbot, operations, page)
    posts = [row["body"] for row in transport.calls if row["path"] == "/responses"]
    assert [p["text"]["format"]["name"] for p in posts] == ["QFILE_PLAN_V1", "FILE_CONTENT_V1"]
    content_input = json.loads(posts[1]["input"][0]["content"][0]["text"])
    assert content_input["plan"] == plan_value()["result"]["data"]
    result = page.result.value
    assert result["status"] == "files_complete_unverified"
    record = list(operations.records.values())[-1]
    assert record.terminal == "files_complete_unverified"
    publish_staged_run(tmp_path / "LOG" / record.identifier)
    assert (tmp_path / "OUT" / "výsledek/navrh.md").read_bytes() == "# Výsledek\nPřesné bytes.\n".encode()


def foreground_process(root, mode, phase):
    transport = WorkflowHttp(root, fault="after_dispatch" if phase == "dispatch" else "")
    template = make_worker(root / ("template-" + phase), mode)
    cfg = template.cfg
    if mode == "QFILE":
        cfg.qfile_output_path, cfg.qfile_output_format = "result.md", "md"
    if phase == "recover":
        before = (root / "LOG" / "RUN_FOREGROUND" / "run_state.json").read_bytes()
        with pytest.raises(ValueError, match="bezpečně připravenou"):
            RunLogger(str(root / "LOG"), "RUN_FOREGROUND", "test", resume=True)
        assert (root / "LOG" / "RUN_FOREGROUND" / "run_state.json").read_bytes() == before
        assert transport.calls == []
        return
    logger = RunLogger(str(root / "LOG"), "RUN_FOREGROUND", "test")
    executor = RunExecutor(cfg, template.settings, "synthetic-offline", logger)
    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=http_client(transport)):
        executor.run()
    raise AssertionError("Řízený dispatch měl ukončit proces kódem 77")


@pytest.mark.parametrize("mode", ["QA", "QFILE"])
def test_foreground_crash_after_dispatch_new_process_never_resubmits(tmp_path, mode):
    code = "from pathlib import Path; import sys; from test_qa_qfile_http_closure import foreground_process; foreground_process(Path(sys.argv[1]), " + repr(mode) + ", 'dispatch')"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
    before = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert before.returncode == 77, before.stdout + before.stderr
    child("from pathlib import Path; import sys; from test_qa_qfile_http_closure import foreground_process; foreground_process(Path(sys.argv[1]), " + repr(mode) + ", 'recover')", tmp_path)
    calls = [json.loads(line) for line in (tmp_path / "workflow-http.jsonl").read_text().splitlines()]
    assert sum(row["path"] == "/responses" for row in calls) == 1
