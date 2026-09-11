import importlib
import pkgutil
import time
from pathlib import Path
from unittest.mock import patch, Mock

import pytest
import kajovo
from kajovo.core.config import AppSettings
from kajovo.desktop.application import MainWindow
from kajovo.desktop.dialogs import ProgressDialog
from kajovo.desktop.history import ResponseRequestPanel
from kajovo.desktop.jobs import Jobs


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = AppSettings(
        log_dir=str(tmp_path / "LOG"),
        cache_dir=str(tmp_path / "cache"),
    )
    with (
        patch("kajovo.desktop.application.get_secret", return_value=None),
    ):
        widget = MainWindow(settings)
    qtbot.addWidget(widget)
    yield widget
    for manager in widget.findChildren(Jobs):
        for job in list(manager.active):
            job.request_stop()
            job.wait(5000)
    qtbot.wait(10)


def test_all_package_modules_import():
    for module in pkgutil.walk_packages(kajovo.__path__, kajovo.__name__ + "."):
        if not module.name.endswith(".__main__"):
            importlib.import_module(module.name)


def test_main_window_controls_and_state(window):
    assert window.stack.count() == 9
    assert not hasattr(window, "pricing_panel")
    assert not hasattr(window, "db")
    assert window.sp_temp is not window.settings_page.default_temperature
    window._set_active_model("gpt-4.1")
    window.sp_temp.setValue(0.7)
    window.settings_page.default_temperature.setValue(0.3)
    state = window._gather_state()
    window._apply_state(state)
    assert window.sp_temp.value() == 0.7
    assert window.settings_page.default_temperature.value() == 0.3
    window.chk_in_eq_out.setChecked(True)
    window.ed_in.setText("input-path")
    assert window.ed_out.text() == "input-path"
    assert not state["ssh_password"]


def test_missing_saved_override_is_not_replaced_by_main_model(window):
    window._set_generate_model_override(window.cb_model_a1, "missing-model")
    assert window._get_generate_model_override(window.cb_model_a1) == "missing-model"
    assert not window.cb_model_a1.model().item(window.cb_model_a1.currentIndex()).isEnabled()


def test_api_save_activates_key_only_after_verified_storage(window, monkeypatch):
    import os

    events = []
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-old-key")
    window.settings_page.api_key.setText("dummy-new-key")

    def persist(value):
        assert os.environ["OPENAI_API_KEY"] == "dummy-old-key"
        events.append(("persist", value))
        return True

    monkeypatch.setattr("kajovo.desktop.settings.persist_api_key", persist)
    monkeypatch.setattr(window, "_refresh_models_best_effort", lambda: None)
    monkeypatch.setattr("kajovo.desktop.settings.msg_info", lambda *args: None)
    window.settings_page.key_changed.connect(
        lambda key: events.append(("activate", os.environ["OPENAI_API_KEY"]))
    )
    window.settings_page.save_key()
    assert events == [("persist", "dummy-new-key"), ("activate", "dummy-new-key")]


@pytest.mark.parametrize("inherited", ["", "dummy-stale-key"])
def test_saved_key_and_matrix_survive_window_restart(window, qtbot, monkeypatch, inherited):
    import os

    stored = {}
    monkeypatch.setattr(
        "kajovo.core.secret_store._read_persisted_api_key", lambda: stored.get("key")
    )
    monkeypatch.setattr(MainWindow, "_refresh_models_best_effort", lambda self: None)
    monkeypatch.setattr("kajovo.desktop.settings.msg_info", lambda *args: None)

    def persist(value):
        stored["key"] = value
        return True

    monkeypatch.setattr("kajovo.desktop.settings.persist_api_key", persist)
    window.settings_page.api_key.setText("dummy-persistent-key")
    window.settings_page.save_key()
    assert window.api_key == "dummy-persistent-key"
    window.close()
    monkeypatch.setenv("OPENAI_API_KEY", inherited)
    with patch("kajovo.desktop.application.get_secret", return_value=None):
        reopened = MainWindow(window.s)
    qtbot.addWidget(reopened)
    assert reopened.api_key == stored["key"] == os.environ["OPENAI_API_KEY"]
    assert reopened.settings_page.api_key.text() == stored["key"]
    assert all(
        panel.api_key == stored["key"]
        for panel in (reopened.files_panel, reopened.vector_panel, reopened.batch_panel)
    )
    assert reopened.caps_cache.get("gpt-5.2").ok_basic


def test_static_matrix_available_without_probe(window):
    window.all_models = ["gpt-5.2", "unknown-model"]
    window._refresh_model_tab()
    assert window.lst_models.count() == 2
    assert "není určen" in window.lst_models.item(1).text()
    assert window.caps_cache.get("gpt-5.2").ok_basic
    assert "unknown-model" not in window._current_model_list()
    assert not hasattr(window, "probe_worker")


@pytest.mark.parametrize("action", ["save_key", "delete_key"])
def test_failed_api_storage_does_not_activate_or_report_success(window, monkeypatch, action):
    import os
    from kajovo.core.secret_store import APIKeyStoreError

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-old-key")
    window.settings_page.api_key.setText("dummy-new-key")

    def fail(value):
        raise APIKeyStoreError("Uložení selhalo.")

    errors, activated, successes = [], [], []
    monkeypatch.setattr("kajovo.desktop.settings.persist_api_key", fail)
    monkeypatch.setattr(
        "kajovo.desktop.settings.msg_warning", lambda *args: errors.append(args[-1])
    )
    monkeypatch.setattr("kajovo.desktop.settings.msg_info", lambda *args: successes.append(True))
    window.settings_page.key_changed.connect(lambda key: activated.append(key))
    getattr(window.settings_page, action)()
    assert errors and not activated and not successes
    assert os.environ["OPENAI_API_KEY"] == "dummy-old-key"


def test_window_fits_small_screen_and_selects_pages(window, qtbot):
    window.resize(1100, 700)
    window.show()
    qtbot.wait(20)
    assert window.height() <= 700
    window.select_page("history")
    assert window.stack.currentWidget() is window.pages["history"][0]
    window.select_page("run")
    assert window.stack.currentWidget() is window.tab_run


@pytest.mark.parametrize(
    "size", [(1366, 720), (1093, 576), (911, 480), (1920, 1032), (1536, 826), (1280, 688)]
)
def test_run_actions_fit_target_logical_viewports(window, qtbot, size):
    from PySide6.QtCore import QPoint, QRect

    window.resize(*size)
    window.show()
    qtbot.wait(30)
    assert window.width() <= size[0] and window.height() <= size[1]
    for action in (window.btn_go, window.btn_stop, window.runs_button):
        assert window.rect().contains(QRect(action.mapTo(window, QPoint()), action.size()))
        assert action.isVisible()


def test_progress_uses_completed_units_instead_of_start_message(qtbot):
    from kajovo.core.progress import ProgressEvent

    dialog = ProgressDialog()
    qtbot.addWidget(dialog)
    dialog.set_status("A3: FILE src/main.py (2/4)")
    assert dialog.pb_sub.maximum() == 0
    dialog.on_progress_event(ProgressEvent("A3", completed=1, total=4, unit="souborů"))
    assert dialog.pb_sub.value() == 1 and dialog.pb_sub.maximum() == 4


def test_long_modal_content_has_scrollable_full_text(window, qtbot):
    from kajovo.desktop.dialogs import DetailDialog

    content = "Dlouhá zpráva se musí celá zobrazit. " * 200
    dialog = DetailDialog("Dlouhá zpráva", content, window, details="Podklady\n" * 50)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(30)
    assert dialog.browser.toPlainText() == content
    assert dialog.browser.verticalScrollBar().maximum() > 0
    dialog.technical.setChecked(True)
    assert "Podklady" in dialog.browser.toPlainText()


def test_pages_restore_visible_content_after_resize(window, qtbot):
    window.show()
    for key in ("cascade", "resources", "history"):
        window.select_page(key)
        for width in (911, 1920, 911, 1920):
            window.resize(width, 800)
            qtbot.wait(20)
            assert window.stack.currentWidget().isVisible()


@pytest.mark.parametrize("value", ["2026-09-09", "09.09.2026", "09092026"])
def test_date_filter(value):
    assert ResponseRequestPanel._normalize_date_filter(value) == "2026-09-09"


def test_rerun_does_not_skip_response_without_written_file(window, tmp_path):
    import json

    run = Path(window.s.log_dir) / "RUN_090920261200_ABCD"
    responses = run / "responses"
    responses.mkdir(parents=True)
    (responses / "A3_FILE.json").write_text(
        json.dumps({"output_text": json.dumps({"path": "missing.py", "content": "partial"})}),
        encoding="utf-8",
    )
    assert window._gather_completed_paths(run.name, str(tmp_path)) == []


def test_pending_result_is_information_and_stops_progress(window, qtbot):
    from kajovo.core.progress import ProgressEvent
    dialog = ProgressDialog(window)
    qtbot.addWidget(dialog)
    window._run_contexts["test"] = {"dialog": dialog}
    try:
        with patch("kajovo.desktop.application.msg_info") as info, patch(
            "kajovo.desktop.application.msg_critical"
        ) as critical:
            window.on_run_ok("test", {
                "status": "preflight_pending", "detail": "Ověření probíhá.",
                "preflight_batches": [{"id": "batch_trial", "status": "validating"}],
            })
        info.assert_called_once_with(
            window, "Ověření dávky probíhá", "Ověření probíhá.",
            details=[{"id": "batch_trial", "status": "validating"}],
        )
        critical.assert_not_called()
        assert dialog.clock.finished is not None
        assert not dialog.btn_stop.isEnabled()
        assert "Čeká na ověření" in dialog.lbl_stage.text()
        dialog.on_progress_event(ProgressEvent("RUN", "preflight_pending"))
    finally:
        window._run_contexts.clear()


@pytest.mark.parametrize("status", ["preflight_pending", "batch_pending", "completed", "failed", "cancelled"])
def test_finished_progress_offers_only_ok(qtbot, status):
    from kajovo.core.progress import ProgressEvent
    from PySide6.QtCore import Qt

    dialog = ProgressDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.btn_stop.isVisible()
    assert dialog.btn_close.text() == "Skrýt průběh"
    stopped = Mock()
    dialog.btn_stop.clicked.connect(stopped)
    dialog.on_progress_event(ProgressEvent("RUN", status))
    assert not dialog.btn_stop.isVisible()
    assert not dialog.chk_bzz.isVisible()
    assert dialog.btn_close.text() == "OK"
    assert dialog.btn_close.isEnabled()
    qtbot.mouseClick(dialog.btn_close, Qt.LeftButton)
    assert not dialog.isVisible()
    stopped.assert_not_called()


@pytest.mark.parametrize("status", ["failed", "preflight_pending"])
def test_rerun_passes_full_saved_batch(window, monkeypatch, status):
    import json
    from test_generate_batch import manifest
    run_id = "RUN_090920261200_ABCD"
    directory = Path(window.s.log_dir) / run_id
    directory.mkdir()
    state = {"run_id": run_id, "status": status, "generate_batch": manifest(),
             "ui_state": {"mode": "GENERATE", "send_as_c": True}}
    (directory / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    observed = []
    monkeypatch.setattr(window, "_apply_state", lambda ui: None)
    monkeypatch.setattr(window, "on_go", lambda: observed.append(window._resume_batch_state))
    window.rerun(run_id)
    assert observed == [state]
    assert window._resume_batch_state is None


def test_rerun_of_sent_batch_does_not_start_worker(window, monkeypatch):
    import json
    run_id = "RUN_090920261200_ABCD"
    directory = Path(window.s.log_dir) / run_id
    directory.mkdir()
    (directory / "run_state.json").write_text(
        json.dumps({"generate_batch": {"version": 1}, "batch_id": "batch_sent"}), encoding="utf-8"
    )
    start = Mock()
    monkeypatch.setattr(window, "on_go", start)
    with patch.object(window.batch_panel, "complete_run") as complete:
        window.rerun(run_id)
    start.assert_not_called()
    complete.assert_called_once_with(run_id)


def test_git_refresh_does_not_change_remote(window, qtbot):
    import subprocess

    panel = window.git_panel
    calls = []

    def fake(args, cwd=None):
        calls.append(args)
        result = str(Path.cwd()) if args == ["rev-parse", "--show-toplevel"] else ""
        return subprocess.CompletedProcess(args, 0, result, "")

    with patch.object(panel, "_run_git", side_effect=fake):
        panel.ed_remote.setText("git@github.com:owner/example.git")
        panel.refresh()
        qtbot.waitUntil(lambda: not panel.jobs.active)
    assert not any(
        args[:2] in (["remote", "remove"], ["remote", "add"], ["remote", "set-url"])
        for args in calls
    )


def test_reasoning_model_disables_default_temperature(window):
    window._set_active_model("gpt-6-astra")
    assert not window.sp_temp.isEnabled()
    window._set_active_model("gpt-4.1-nano")
    assert window.sp_temp.isEnabled()


def test_qa_cannot_submit_file_batch(window):
    window.chk_send_as_c.setChecked(True)
    window.cb_mode.setCurrentText("QA")
    assert not window.chk_send_as_c.isEnabled() and not window.chk_send_as_c.isChecked()


def test_batch_native_controls(window):
    window.cb_mode.setCurrentText("GENERATE")
    window.chk_diag_win_in.setChecked(True)
    window.ed_response_id.setText("resp_existing")
    window.chk_send_as_c.setChecked(True)
    assert window.ed_response_id.isEnabled() and window.row_generate_models.isEnabled()
    assert window.chk_diag_win_in.isChecked() and window.chk_diag_win_in.isEnabled()
    assert not window.chk_diag_win_out.isEnabled()
    window.chk_send_as_c.setChecked(False)
    assert window.ed_response_id.text() == "resp_existing"


def test_cascade_temperature_and_first_response(window):
    panel = window.cascade_panel
    panel.cb_step_model.addItem("gpt-6-astra")
    panel.cb_step_model.setCurrentText("gpt-6-astra")
    assert not panel.chk_step_temp.isEnabled() and not panel.chk_step_temp.isChecked()
    assert not panel.sp_step_temp.isEnabled()
    assert panel.ed_prev_resp.isEnabled()


def test_cascade_failed_save_preserves_step(window):
    import copy

    panel = window.cascade_panel
    before = copy.deepcopy(panel.definition.steps[0])
    panel.ed_step_title.setText("Changed")
    panel.schema_kind.setCurrentText("custom")
    with patch("kajovo.desktop.cascades.msg_warning"):
        assert panel.save_current_step() is False
    assert panel.definition.steps[0] == before


def test_cascade_preserves_model_missing_from_catalog(window):
    panel = window.cascade_panel
    panel.definition.steps[0].model = "private-model-snapshot"
    panel.on_step_selected(0)
    assert panel.cb_step_model.currentText() == "private-model-snapshot"
    with patch("kajovo.desktop.cascades.msg_warning"):
        assert not panel.save_current_step()
    assert panel.definition.steps[0].model == "private-model-snapshot"


def test_batch_monitor_uses_settings_and_stops_at_deadline(window):
    panel = window.batch_panel
    panel.api_key = "test"
    panel.s.batch_poll_interval_s = 7
    panel.s.batch_timeout_s = 60
    panel._monitor_started = time.monotonic()
    result = {"key": "test", "batches": [{"id": "batch_test", "status": "in_progress"}]}
    panel._on_refreshed(result)
    assert panel._poll_timer.isActive() and panel._poll_timer.interval() == 7000
    panel._poll_timer.stop()
    panel._monitor_started = time.monotonic() - 61
    panel._on_refreshed(result)
    assert not panel._poll_timer.isActive()


def test_enabled_action_buttons_have_connected_handlers(window):
    from PySide6.QtWidgets import QPushButton

    unconnected = [
        action.text()
        for action in window.findChildren(QPushButton)
        if action.isEnabled()
        and not action.receivers("2clicked(bool)")
        and not action.receivers("2pressed()")
    ]
    assert not unconnected


def test_batch_rejects_incomplete_output_before_file_processing(window, tmp_path):
    import json
    from kajovo.desktop.batches import import_bundle

    raw = json.dumps(
        {"response": {"body": {"status": "incomplete", "output_text": '{"files":[]}'}}}
    ).encode()
    result = import_bundle(raw, str(tmp_path))
    assert result["errors"] and not result["written"]


def test_hybrid_import_runs_off_ui_thread(window, qtbot):
    import threading

    panel = window.batch_panel
    panel._on_refreshed({"key": panel.api_key, "batches": [{"id": "batch_external", "status": "completed"}]})
    panel.tbl.selectRow(0)
    threads = []

    def operation(client):
        threads.append(threading.get_ident())
        return {"written": [], "errors": {}, "status": "partial"}

    with (
        patch("kajovo.desktop.batches.OpenAIClient", return_value=Mock()),
        patch("kajovo.desktop.batches.msg_info"),
        patch.object(panel, "load"),
    ):
        panel._start_operation(operation)
        assert not panel.btn_download.isEnabled()
        qtbot.waitUntil(lambda: panel._operation_task is None, timeout=5000)
    assert threads and threads[0] != threading.get_ident()
    assert panel.btn_download.isEnabled()


def test_modify_batch_still_disables_live_preparation(window):
    window.cb_mode.setCurrentText("MODIFY")
    window.chk_send_as_c.setChecked(True)
    assert not window.ed_response_id.isEnabled()
    assert not window.chk_diag_win_in.isEnabled()
    assert not window.row_generate_models.isEnabled()
