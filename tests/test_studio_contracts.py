"""Regrese nových vazeb UI: skutečné Qt signály, soubory a účetní záznamy."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtWidgets import QDialog
from kajovo.desktop.finance import EstimateDialog
from kajovo.desktop.jobs import Job
from kajovo.desktop.windows import DetachedPageDialog
import test_desktop

window = test_desktop.window
from test_cost_dialog import controller
from test_output_chunks import raw
from kajovo.desktop.batches import import_bundle


def test_confirmation_crosses_gui_thread_before_request(qtbot, tmp_path, monkeypatch):
    control = controller(tmp_path, monkeypatch)
    gui = threading.get_ident()
    approvals, calls, errors = [], [], []

    def approve(dialog):
        approvals.append(threading.get_ident())
        return QDialog.Accepted

    monkeypatch.setattr(EstimateDialog, "exec", approve)

    class Client:
        def validate_access(self, payload, batch=False):
            from kajovo.core.request_rules import validate_response_payload

            validate_response_payload(payload, batch)

        def count_input_tokens(self, payload):
            return 1000

        def create_response(self, payload):
            assert approvals == [gui]
            calls.append(threading.get_ident())
            return {
                "id": "resp_gui",
                "model": "gpt-4.1",
                "status": "completed",
                "output_text": '{"text":"OK"}',
                "usage": {"input_tokens": 1000, "output_tokens": 10},
            }

    results = []
    job = Job(
        lambda job: control.execute(
            Client(), {"model": "gpt-4.1", "input": "text", "max_output_tokens": 100}
        )
    )
    job.result.connect(results.append)
    job.error.connect(errors.append)
    job.start()
    qtbot.waitUntil(lambda: bool(results or errors), timeout=5000)
    job.wait()
    assert not errors
    assert calls and calls[0] != gui
    assert control.db.query()[0]["total_usd"] == "0.00102"


def test_invalid_bundle_never_partially_overwrites_files(tmp_path):
    target = tmp_path / "first.txt"
    target.write_text("original", encoding="utf-8")
    result = import_bundle(
        raw(
            {
                "contract": "C_FILES_ALL",
                "files": [
                    {"path": "first.txt", "content": "changed"},
                    {"path": "second.txt", "content": 123},
                ],
            }
        ),
        str(tmp_path),
    )
    assert result["errors"] and not result["written"]
    assert target.read_text(encoding="utf-8") == "original"


def test_overlapping_output_rejected_before_worker_creation(window, monkeypatch):
    window.api_key = "test"
    window.ed_project.setText("test")
    window.txt_prompt.setPlainText("Vytvoř soubor.")
    window.all_models = ["gpt-4.1"]
    window._set_active_model("gpt-4.1")
    window.ed_out.setText(str(Path(window.s.log_dir) / "out"))
    other = SimpleNamespace(
        cfg=SimpleNamespace(
            out_dir=window.ed_out.text() + "/nested", mode="GENERATE", send_as_c=False
        )
    )
    window._run_contexts["existing"] = {"worker": other}
    messages = []
    monkeypatch.setattr(
        "kajovo.desktop.application.msg_warning",
        lambda parent, title, message: messages.append(message),
    )
    monkeypatch.setattr(
        "kajovo.desktop.application.RunWorker",
        lambda *args: (_ for _ in ()).throw(AssertionError("Worker nesmí vzniknout.")),
    )
    try:
        window.on_go()
    finally:
        window._run_contexts.clear()
    assert any("OUT" in message for message in messages), messages
    assert not any("Worker" in message for message in messages)


def test_empty_modify_input_is_not_current_directory(window):
    import pytest

    window.ed_in.clear()
    with pytest.raises(ValueError, match="IN"):
        window._validate_paths("MODIFY", False)


def test_state_retains_settings_but_never_passwords(window):
    window.settings_page.smtp["password"].setText("dummy-smtp-secret")
    window.ed_ssh_pwd.setText("dummy-ssh-secret")
    window.settings_page.default_temperature.setValue(0.8)
    saved = window._gather_state()
    assert "dummy-smtp-secret" not in json.dumps(saved)
    assert "dummy-ssh-secret" not in json.dumps(saved)
    window.settings_page.default_temperature.setValue(0.1)
    window._apply_state(saved)
    assert window.settings_page.default_temperature.value() == 0.8


def test_detached_settings_return_to_original_container(window, qtbot):
    owner = window.pages["settings"][0]
    page = owner.widget()
    window.open_section("settings")
    assert owner.widget() is None
    dialog = window.findChild(DetachedPageDialog)
    assert dialog.page is page
    dialog.accept()
    assert owner.widget() is page


def test_cascade_rejects_future_json_reference_without_changing_step(window):
    panel = window.cascade_panel
    before = panel.get_definition().steps[0]
    panel._select_model("gpt-4.1")
    panel.txt_input_text.setPlainText("{{step.2.json}}")
    with patch("kajovo.desktop.cascades.msg_warning") as warning:
        assert not panel.save_current_step()
    assert warning.called and panel.definition.steps[0] == before


def test_cascade_text_output_cannot_claim_expected_files(window):
    panel = window.cascade_panel
    panel._select_model("gpt-4.1")
    panel.expected_files.setPlainText("result.py")
    with patch("kajovo.desktop.cascades.msg_warning"):
        assert not panel.save_current_step()
    assert not panel.definition.steps[0].expected_out_files


def test_recovery_uses_events_and_related_structure(tmp_path):
    import json
    from kajovo.desktop.recovery import recover_run

    current = tmp_path / "run"
    (current / "requests").mkdir(parents=True)
    (current / "requests" / "01.json").write_text(
        json.dumps({"ui_state": {"out_dir": str(tmp_path / "OUT"), "project": "demo"}})
    )
    (current / "events.jsonl").write_text(
        json.dumps(
            {"type": "api.trace", "data": {"action": "complete", "response_id": "resp_latest"}}
        )
        + "\ninvalid"
    )
    related = tmp_path / "related"
    (related / "manifests").mkdir(parents=True)
    (related / "run_state.json").write_text(json.dumps({"out_dir": str(tmp_path / "OUT")}))
    (related / "manifests" / "resume_structure.json").write_text(
        json.dumps({"resume_files": [{"path": "main.py"}], "resume_prev_id": "resp_old"})
    )
    ui, previous, files, scope = recover_run(tmp_path, "run")
    assert ui["project"] == "demo"
    assert previous == "resp_latest"
    assert files == [{"path": "main.py"}]
    assert scope == "run"


def test_recovery_saved_map_rejects_unsafe_paths(tmp_path):
    import json
    from kajovo.desktop.recovery import recover_run

    current = tmp_path / "run"
    (current / "requests").mkdir(parents=True)
    (current / "manifests").mkdir()
    (current / "requests" / "01.json").write_text(
        json.dumps({"ui_state": {"out_dir": str(tmp_path / "OUT")}})
    )
    (current / "manifests" / "01_out_saved_map.json").write_text(
        json.dumps({"saved": [{"path": "../escape"}, {"path": "valid.py"}]})
    )
    assert recover_run(tmp_path, "run")[2] == [{"path": "valid.py", "purpose": ""}]
