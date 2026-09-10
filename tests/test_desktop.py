import importlib
import pkgutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import kajovo
from kajovo.core.config import AppSettings
from kajovo.ui.mainwindow import MainWindow
from kajovo.ui.progress_dialog import ProgressDialog
from kajovo.ui.response_request_panel import ResponseRequestPanel


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"), db_path=str(tmp_path / "data.sqlite"))
    settings.pricing.auto_refresh_on_start = False
    with patch.object(MainWindow, "_start_pricing_audit_loop"), patch("kajovo.ui.mainwindow.get_secret", return_value=None):
        widget = MainWindow(settings)
    qtbot.addWidget(widget)
    return widget


def test_all_package_modules_import():
    for module in pkgutil.walk_packages(kajovo.__path__, kajovo.__name__ + "."):
        if not module.name.endswith(".__main__"):
            importlib.import_module(module.name)


def test_main_window_controls_and_state(window):
    assert window.tabs.count() == 12
    assert window.sp_temp is not window.sp_default_temp
    window.sp_temp.setValue(0.7)
    window.sp_default_temp.setValue(0.3)
    state = window._gather_state()
    window._apply_state(state)
    assert window.sp_temp.value() == 0.7
    assert window.sp_default_temp.value() == 0.3
    window.chk_in_eq_out.setChecked(True)
    window.ed_in.setText("input-path")
    assert window.ed_out.text() == "input-path"


def test_missing_saved_override_is_not_replaced_by_main_model(window):
    window._set_generate_model_override(window.cb_model_a1, "missing-model")
    assert window._get_generate_model_override(window.cb_model_a1) == "missing-model"
    assert not window.cb_model_a1.model().item(window.cb_model_a1.currentIndex()).isEnabled()


def test_api_save_activates_key_only_after_verified_storage(window, monkeypatch):
    import os
    events = []
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-old-key")
    window.ed_settings_apikey.setText("dummy-new-key")
    def persist(value):
        assert os.environ["OPENAI_API_KEY"] == "dummy-old-key"
        events.append(("persist", value))
        return True
    monkeypatch.setattr(window, "_set_env_api_key", persist)
    monkeypatch.setattr(window, "_apply_api_key_change", lambda: events.append(("activate", os.environ["OPENAI_API_KEY"])))
    monkeypatch.setattr("kajovo.ui.mainwindow.msg_info", lambda *args: None)
    window._api_save()
    assert events == [("persist", "dummy-new-key"), ("activate", "dummy-new-key")]


@pytest.mark.parametrize("inherited", ["", "dummy-stale-key"])
def test_saved_key_and_probe_survive_window_restart(window, qtbot, monkeypatch, inherited):
    import os
    stored = {}
    monkeypatch.setattr("kajovo.core.secret_store._read_persisted_api_key", lambda: stored.get("key"))
    monkeypatch.setattr("kajovo.ui.filepanel.FilesPanel.refresh", lambda self: None)
    monkeypatch.setattr("kajovo.ui.vectorstores_panel.VectorStoresPanel.refresh", lambda self: None)
    monkeypatch.setattr("kajovo.ui.batch_panel.BatchPanel.load", lambda self, *args, **kwargs: None)
    monkeypatch.setattr("kajovo.core.openai_client.OpenAIClient.list_models", lambda self: [{"id": "gpt-5.2"}])
    monkeypatch.setattr("kajovo.ui.mainwindow.msg_info", lambda *args: None)
    monkeypatch.setattr(MainWindow, "_start_pricing_audit_loop", lambda self: None)
    def persist(value):
        stored["key"] = value
        return True
    monkeypatch.setattr(window, "_set_env_api_key", persist)
    window.ed_settings_apikey.setText("dummy-persistent-key")
    window._api_save()
    assert window.api_key == "dummy-persistent-key"
    window.close()
    monkeypatch.setenv("OPENAI_API_KEY", inherited)
    with patch("kajovo.ui.mainwindow.get_secret", return_value=None):
        reopened = MainWindow(window.s)
    qtbot.addWidget(reopened)
    assert reopened.api_key == stored["key"] == os.environ["OPENAI_API_KEY"]
    assert reopened.ed_settings_apikey.text() == stored["key"]
    assert reopened.files_panel.api_key == stored["key"]
    assert reopened.vector_panel.api_key == stored["key"]
    assert reopened.batch_panel.api_key == stored["key"]
    assert reopened.caps_cache.get("gpt-5.2").ok_basic


def test_static_matrix_available_without_probe(window):
    window.all_models = ["gpt-5.2", "unknown-model"]
    window.chk_model_include_untested.setChecked(False)
    window._apply_model_filter()
    assert window.lst_models.count() == 1
    assert "gpt-5.2" in window.lst_models.item(0).text()
    assert window.caps_cache.get("gpt-5.2").ok_basic
    assert window.cb_model.findText("unknown-model") == -1
    assert not hasattr(window, "probe_worker")


@pytest.mark.parametrize("action", ["_api_save", "_api_delete"])
def test_failed_api_storage_does_not_activate_or_report_success(window, monkeypatch, action):
    import os
    from kajovo.core.secret_store import APIKeyStoreError
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-old-key")
    window.ed_settings_apikey.setText("dummy-new-key")
    def fail(value):
        raise APIKeyStoreError("Uložení selhalo.")
    errors, activated, successes = [], [], []
    monkeypatch.setattr(window, "_set_env_api_key", fail)
    monkeypatch.setattr(window, "_apply_api_key_change", lambda: activated.append(True))
    monkeypatch.setattr("kajovo.ui.mainwindow.msg_critical", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr("kajovo.ui.mainwindow.msg_info", lambda *args: successes.append(True))
    getattr(window, action)()
    assert errors and not activated and not successes
    assert os.environ["OPENAI_API_KEY"] == "dummy-old-key"


def test_window_fits_small_screen_and_selects_wrapped_tabs(window, qtbot):
    window.resize(1100, 700)
    window.show()
    qtbot.wait(20)
    assert window.height() <= 700
    window.on_pricing()
    assert window.tabs.currentWidget().widget() is window.tab_pricing
    window._select_tab(window.tab_run)
    assert window.tabs.currentWidget().widget() is window.tab_run


@pytest.mark.parametrize("size", [(1366, 720), (1093, 576), (911, 480), (1920, 1032), (1536, 826), (1280, 688)])
def test_run_actions_fit_target_logical_viewports(window, qtbot, size):
    from PySide6.QtCore import QPoint, QRect
    window.resize(*size)
    window.show()
    qtbot.wait(30)
    scroll = window.tabs.widget(0)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() == 0
    viewport = scroll.viewport()
    for button in (window.btn_go, window.btn_stop):
        rect = QRect(button.mapTo(viewport, QPoint()), button.size())
        assert viewport.rect().contains(rect)


def test_progress_uses_completed_units_instead_of_start_message(qtbot):
    from kajovo.core.progress import ProgressEvent
    dialog = ProgressDialog()
    qtbot.addWidget(dialog)
    dialog.set_status("A3: FILE src/main.py (2/4)")
    assert "2/4" not in dialog.lbl_file.text()
    dialog.on_progress_event(ProgressEvent("A3", completed=1, total=4, unit="souborů"))
    assert dialog.pb_sub.value() == 1
    assert "1/4" in dialog.pb_sub.format()


def test_long_modal_content_is_reparented_inside_viewport(window, qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMessageBox, QScrollArea
    from kajovo.ui.widgets import StyledMessageDialog
    dialog = StyledMessageDialog(window, "Dlouhá zpráva", "Dlouhá zpráva se musí celá zobrazit. " * 40,
                                 buttons=[("OK", QMessageBox.Ok)], details="Podklady\n" * 50)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(30)
    scroll = dialog.findChild(QScrollArea)
    assert dialog.lbl.parentWidget() is scroll.widget()
    assert dialog.lbl.height() >= dialog.lbl.heightForWidth(dialog.lbl.width())
    title = dialog.titlebar.lbl
    assert title.width() > 100
    point = title.mapTo(dialog, QPoint(5, 5))
    assert dialog.childAt(point) is title


def test_adaptive_panels_restore_visible_content_after_resize(window, qtbot):
    from kajovo.ui.layouts import AdaptivePanels
    window.show()
    for tab in (window.tab_cascade, window.tab_vector):
        window._select_tab(tab)
        for width in (911, 1920, 911, 1920):
            window.resize(width, 800)
            qtbot.wait(30)
            for panels in tab.findChildren(AdaptivePanels):
                if panels.compact:
                    assert panels.tabs.currentWidget().isVisible()
                else:
                    assert all(panel.isVisible() for panel in panels.panels)


@pytest.mark.parametrize("value", ["2026-09-09", "09.09.2026", "09092026"])
def test_date_filter(value):
    assert ResponseRequestPanel._normalize_date_filter(None, value) == "20260909"


def test_rerun_does_not_skip_response_without_written_file(window, tmp_path):
    import json
    run = Path(window.s.log_dir) / "RUN_090920261200_ABCD"
    responses = run / "responses"
    responses.mkdir(parents=True)
    body = {"path": "missing.py", "content": "partial"}
    (responses / "A3_FILE.json").write_text(json.dumps({"output": [{"content": [{"text": json.dumps(body)}]}]}), encoding="utf-8")
    assert window._gather_completed_paths(run.name, str(tmp_path)) == []


def test_git_refresh_does_not_change_remote(window):
    import subprocess
    panel = window.git_panel
    calls = []
    def fake(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    with patch.object(panel, "_is_git_repo", return_value=True), patch.object(panel, "_run_git", side_effect=fake):
        panel.ed_remote.setText("git@github.com:owner/example.git")
        panel._check_sync()
    assert not any(args[:2] in (["remote", "remove"], ["remote", "add"], ["remote", "set-url"]) for args in calls)


def test_reasoning_model_disables_default_temperature(window):
    window.on_model_changed("gpt-6-astra")
    assert not window.sp_temp.isEnabled()
    window.on_model_changed("gpt-4.1-nano")
    assert window.sp_temp.isEnabled()


def test_qa_cannot_submit_file_batch(window):
    window.chk_send_as_c.setChecked(True)
    window.on_mode_changed("QA")
    assert not window.chk_send_as_c.isEnabled()
    assert not window.chk_send_as_c.isChecked()


def test_batch_native_controls(window):
    window.cb_mode.setCurrentText("GENERATE")
    window.chk_diag_win_in.setChecked(True)
    window.ed_response_id.setText("resp_existing")
    window.chk_send_as_c.setChecked(True)
    assert window.ed_response_id.isEnabled()
    assert window.row_generate_models.isEnabled()
    assert window.chk_diag_win_in.isChecked()
    assert window.chk_diag_win_in.isEnabled()
    assert not window.chk_diag_win_out.isEnabled()
    window.chk_send_as_c.setChecked(False)
    assert window.ed_response_id.isEnabled()
    assert window.ed_response_id.text() == "resp_existing"
    assert window.row_generate_models.isEnabled()


def test_cascade_temperature_and_first_response(window):
    panel = window.cascade_panel
    panel.cb_step_model.addItem("gpt-6-astra")
    panel.cb_step_model.setCurrentText("gpt-6-astra")
    assert not panel.chk_step_temp.isEnabled()
    assert not panel.chk_step_temp.isChecked()
    assert not panel.sp_step_temp.isEnabled()
    assert panel.ed_prev_resp.isEnabled()


def test_cascade_failed_save_preserves_step(window):
    import copy
    panel = window.cascade_panel
    before = copy.deepcopy(panel.definition.steps[0])
    panel.ed_step_title.setText("Changed")
    panel.rb_out_json.setChecked(True)
    panel.chk_schema_custom.setChecked(True)
    with patch("kajovo.ui.cascade_panel.msg_warning"):
        assert panel.save_current_step() is False
    assert panel.definition.steps[0] == before


def test_cascade_preserves_model_missing_from_catalog(window):
    panel = window.cascade_panel
    panel.definition.steps[0].model = "private-model-snapshot"
    panel.on_step_selected(0)
    assert panel.cb_step_model.currentText() == "private-model-snapshot"
    with patch("kajovo.ui.cascade_panel.msg_warning"):
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
    assert panel._poll_timer.isActive()
    assert panel._poll_timer.interval() == 7000
    panel._poll_timer.stop()
    panel._monitor_started = time.monotonic() - 61
    panel._on_refreshed(result)
    assert not panel._poll_timer.isActive()


def test_enabled_action_buttons_have_connected_handlers(window):
    from PySide6.QtWidgets import QPushButton
    unconnected = []
    for button in window.findChildren(QPushButton):
        if button.isEnabled() and not button.receivers("2clicked(bool)") and not button.receivers("2pressed()"):
            unconnected.append(button.text())
    assert not unconnected, unconnected


def test_batch_rejects_incomplete_output_before_file_processing(window):
    from kajovo.core.contracts import ContractError
    with pytest.raises(ContractError):
        window.batch_panel._extract_texts({"status": "incomplete", "output_text": '{"files":[]}'})


def test_hybrid_import_runs_off_ui_thread(window, qtbot):
    import threading
    from unittest.mock import Mock
    panel = window.batch_panel
    threads = []
    def operation(client):
        threads.append(threading.get_ident())
        return {"written": [], "errors": {}, "status": "partial"}
    with patch("kajovo.ui.batch_panel.OpenAIClient", return_value=Mock()), patch("kajovo.ui.batch_panel.msg_info"):
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
