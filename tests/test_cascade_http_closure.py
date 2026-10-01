"""Kaskáda přes produkční HTTP, návazné hodnoty a skutečné procesy/Qt."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
import requests
from shiboken6 import isValid
from PySide6.QtWidgets import QPushButton

from kajovo.core.cascade_contract import output_machine_key
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
from kajovo.core.cascade_types import CascadeDecisionOption, CascadeDefinition, CascadeInput, CascadeOutput, CascadeStep
from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.runlog import RunLogger
from kajovo.studio.cascades import CascadesPage
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from test_runtime_end_to_end import ROOT, child

MODEL = "gpt-4o-mini"


def definition():
    seed = CascadeStep(title="Zdroj", model=MODEL, input_text="Vytvoř základ", deterministic=True,
                       outputs=[CascadeOutput(name="Základ", kind="text")])
    decision = CascadeStep(title="Rozhodnutí", model=MODEL, input_text="Rozhodni podle základu", deterministic=True,
        inputs=[CascadeInput(name="Základ", source="output", source_step_id=seed.id, source_output_id=seed.outputs[0].id)],
        outputs=[CascadeOutput(name="Větev", kind="decision", decision_options=[
            CascadeDecisionOption(value="pokračovat", target_step_number=4), CascadeDecisionOption(value="oprava", target_step_number=3)])])
    skipped = CascadeStep(title="Oprava", model=MODEL, input_text="Tento krok se nesmí odeslat", deterministic=True,
                          outputs=[CascadeOutput(kind="text")])
    finish = CascadeStep(title="Výsledek", model=MODEL, input_text="Převezmi přesný základ", deterministic=True,
        inputs=[CascadeInput(name="Základ", source="output", source_step_id=seed.id, source_output_id=seed.outputs[0].id)],
        outputs=[CascadeOutput(name="Výsledek", kind="text")])
    return CascadeDefinition("HTTP návaznost", steps=[seed, decision, skipped, finish])


class CascadeHttp:
    def __init__(self, root, value, fault=""):
        self.root, self.definition, self.fault = root, value, fault
        self.calls = []
        self.submits = 0

    def request(self, method, url, **kwargs):
        path = url.split("/v1", 1)[1]
        body = copy.deepcopy(kwargs.get("json"))
        row = {"method": method, "path": path, "body": body}
        self.calls.append(row)
        with (self.root / "cascade-http.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        if (method, path) == ("GET", "/models"):
            value = {"data": [{"id": MODEL}], "has_more": False}
        elif (method, path) == ("POST", "/responses/input_tokens"):
            value = {"input_tokens": 128}
        elif (method, path) == ("POST", "/responses"):
            self.submits += 1
            assert body["model"] == MODEL and "background" not in body
            assert "previous_response_id" not in body
            keys = set(body["text"]["format"]["schema"]["properties"])
            step = next(s for s in self.definition.steps if output_machine_key(s.outputs[0]) in keys)
            assert step is not self.definition.steps[2]
            if step is not self.definition.steps[0]:
                assert "PŮVOD: přesná česká hodnota" in json.dumps(body["input"], ensure_ascii=False)
            if self.fault == "dispatch":
                os._exit(77)
            if self.fault == "timeout":
                raise requests.Timeout("syntetická neurčitá odpověď")
            answer = "pokračovat" if step.outputs[0].kind == "decision" else "PŮVOD: přesná česká hodnota"
            data = {output_machine_key(step.outputs[0]): answer}
            if self.fault == "repair" and self.submits == 1:
                data = {"foreign": "chybný výstup"}
            if body.get("metadata", {}).get("kajovo_repair_attempt"):
                assert self.submits == 2 and "Oprav předchozí neplatný výstup" in body["instructions"]
            value = {"id": f"resp_cascade_{self.submits}", "model": MODEL, "status": "completed",
                     "output_text": json.dumps(data, ensure_ascii=False), "usage": {"input_tokens": 128, "output_tokens": 32}}
        else:
            raise AssertionError(f"Neočekávaný HTTP request {method} {path}")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(value, ensure_ascii=False).encode()
        return response


def client_for(transport):
    client = OpenAIClient("synthetic", base_url="https://offline.invalid/v1")
    client._sdk = None
    client.session.request = transport.request
    return client


@pytest.mark.parametrize("fault,count", [("", 3), ("repair", 4), ("timeout", 1)])
def test_cascade_actual_ui_worker_http_branch_repair_and_final_state(qtbot, monkeypatch, tmp_path, fault, count):
    value = definition()
    transport = CascadeHttp(tmp_path, value, fault)
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    operations = Operations(None)
    context = StudioContext(settings, operations, api_key="synthetic")
    context.models = [MODEL]
    page = CascadesPage(context)
    operations.setParent(page)
    qtbot.addWidget(page)
    page.definition = value
    page.name.setText(value.name)
    page.output.setText(str(tmp_path / "OUT"))
    page.project.setText("HTTP test")
    page.current_id = None
    page.draw_steps()
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **k: client_for(transport))
    page.findChild(QPushButton, "cascade.start").click()
    qtbot.waitUntil(lambda: bool(operations.records) and not operations.active, timeout=30000)
    record = next(iter(operations.records.values()))
    qtbot.addWidget(record.dialog)
    assert transport.submits == count
    assert record.terminal == ("submission_unknown" if fault == "timeout" else "completed")
    assert not isValid(record.worker) or not record.worker.isRunning()
    adapter = LegacyRunAdapter(tmp_path / "LOG" / record.identifier)
    if fault != "timeout":
        assert set(record.result["executed_step_ids"]) == {value.steps[i].id for i in [0, 1, 3]}
        state = json.loads((adapter.root / "run_state.json").read_text())
        assert state["cascade_runtime"]["values"][value.steps[3].id + "|" + value.steps[3].outputs[0].id]["value"] == "PŮVOD: přesná česká hodnota"
        assert adapter.bundle.verify_integrity()["valid"]
        assert any(row["checkpoint_type"] == "cascade_step_completed" for row in adapter.checkpoints())
    else:
        assert not record.result and record.error
    record.dialog.close()


def cascade_process(root, phase):
    path = root / "definition.json"
    if phase in {"accepted", "dispatch"}:
        value = definition()
        path.write_text(json.dumps(value.to_dict(), ensure_ascii=False))
    else:
        value = CascadeDefinition.from_dict(json.loads(path.read_text()))
        value.run_from_step_id = value.steps[0].id
    transport = CascadeHttp(root, value, "dispatch" if phase == "dispatch" else "")
    worker = CascadeRunExecutor(CascadeRunConfig("HTTP test", value, "", str(root / "OUT")),
                                AppSettings(log_dir=str(root / "LOG")), "synthetic")
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    original = RunLogger.update_state

    def after_cache(logger, patch_value):
        original(logger, patch_value)
        cache = patch_value.get("cascade_runtime")
        if phase == "accepted" and isinstance(cache, dict) and cache.get("primary_responses"):
            os._exit(88)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **k: client_for(transport)), patch.object(RunLogger, "update_state", after_cache):
        worker.execute()
    if phase == "recover_dispatch":
        assert errors and not results and transport.calls == []
    else:
        assert not errors and results, errors
        assert json.loads(Path(worker.logger.state_path).read_text())["status"] == "completed"
        assert transport.submits == 2
        assert LegacyRunAdapter(worker.logger.paths.run_dir).bundle.verify_integrity()["valid"]


@pytest.mark.parametrize("phase,exit_code", [("accepted", 88), ("dispatch", 77)])
def test_cascade_http_hard_exit_reuses_accepted_or_blocks_unknown(tmp_path, phase, exit_code):
    code = "from pathlib import Path; import sys; from test_cascade_http_closure import cascade_process; cascade_process(Path(sys.argv[1]), " + repr(phase) + ")"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
    proc = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == exit_code, proc.stdout + proc.stderr
    child("from pathlib import Path; import sys; from test_cascade_http_closure import cascade_process; cascade_process(Path(sys.argv[1]), " + repr("recover_" + phase) + ")", tmp_path)
    calls = [json.loads(line) for line in (tmp_path / "cascade-http.jsonl").read_text().splitlines()]
    posts = [row for row in calls if row["path"] == "/responses"]
    assert len(posts) == (3 if phase == "accepted" else 1)


def test_cascade_stop_during_http_retains_paid_result_without_next_submit(qtbot, monkeypatch, tmp_path):
    import threading
    from PySide6.QtCore import QCoreApplication, QEvent
    value = definition()
    transport = CascadeHttp(tmp_path,value)
    entered, release = threading.Event(), threading.Event()
    original = transport.request
    def boundary(method,url,**kwargs):
        if method == 'POST' and url.endswith('/responses'):
            entered.set()
            assert release.wait(20), 'HTTP stop bariéra nebyla uvolněna'
        return original(method,url,**kwargs)
    transport.request = boundary
    operations = Operations(None)
    context = StudioContext(AppSettings(log_dir=str(tmp_path/'LOG')),operations,api_key='synthetic')
    context.models = [MODEL]
    page = CascadesPage(context)
    operations.setParent(page)
    qtbot.addWidget(page)
    page.definition, page.current_id = value, None
    page.name.setText(value.name)
    page.output.setText(str(tmp_path/'OUT'))
    page.project.setText('Stop HTTP')
    page.draw_steps()
    monkeypatch.setattr('kajovo.core.cascade_pipeline.OpenAIClient',lambda *a,**k:client_for(transport))
    page.findChild(QPushButton,'cascade.start').click()
    qtbot.waitUntil(entered.is_set)
    record = next(iter(operations.records.values()))
    record.dialog.stop.click()
    release.set()
    qtbot.waitUntil(lambda:not operations.active,timeout=30000)
    qtbot.addWidget(record.dialog)
    assert record.terminal == 'cancelled', (record.terminal,record.error)
    assert transport.submits == 1
    state = json.loads(Path(record.worker.logger.state_path).read_text()) if isValid(record.worker) else json.loads((tmp_path/'LOG'/record.identifier/'run_state.json').read_text())
    assert state['status'] == 'cancelled'
    assert state['cascade_runtime']['primary_responses']
    # Návrat stránky nepřipojí dokončení zrušené operace k novému zadání.
    record.dialog.close()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
