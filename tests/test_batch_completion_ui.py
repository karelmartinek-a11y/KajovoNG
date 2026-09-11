"""Vazby historie, dávek a trvalé předvolby modelu v desktopu."""

import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QPushButton

import test_desktop
from test_batch_completion import modify_output
from kajovo.core.batch_completion import read_state

window = test_desktop.window


def make_history(window, *, status="batch_pending"):
    run_id = "RUN_110920261200_abcd"
    directory = Path(window.s.log_dir) / run_id
    directory.mkdir()
    (directory / "run_state.json").write_text(json.dumps({
        "run_id": run_id, "batch_id": "batch_work", "project": "Ukázkový projekt",
        "out_dir": str(directory.parent / "OUT"), "status": status,
        "batch_records": {"batch_work": {"created_at": 1726050000}},
    }), encoding="utf-8")
    window.history_panel.refresh_runs()
    return directory


def completion_button(window):
    item = window.history_panel.lst_runs.item(0)
    widget = window.history_panel.lst_runs.itemWidget(item)
    return widget.findChild(QPushButton) if widget else None


def test_batch_table_date_project_and_actions(window, qtbot):
    make_history(window)
    panel = window.batch_panel
    panel._on_refreshed({"key": "", "batches": [
        {"id": "batch_work", "status": "completed", "created_at": 1726051000},
        {"id": "batch_external", "status": "in_progress"},
    ]})
    assert panel.tbl.item(0, 2).text() == datetime.fromtimestamp(1726051000).strftime("%d.%m.%Y %H:%M:%S")
    assert panel.tbl.item(0, 8).text() == "Ukázkový projekt"
    assert panel.tbl.cellWidget(0, 10).text() == "Dokončit"
    assert panel.tbl.item(1, 2).text() == "Není dostupné"
    assert panel.tbl.cellWidget(1, 10) is None
    panel.tbl.selectRow(0)
    assert not panel.btn_cancel.isEnabled()
    panel.tbl.selectRow(1)
    assert panel.btn_cancel.isEnabled()
    panel.render_records()
    assert panel.tbl.currentRow() == 1
    panel._on_refreshed({"key": "", "batches": [{"id": "batch_work", "status": "cancelling"}]})
    panel.tbl.selectRow(0)
    assert not panel.btn_cancel.isEnabled()
    assert panel.tbl.item(0, 2).text() == datetime.fromtimestamp(1726051000).strftime("%d.%m.%Y %H:%M:%S")
    window.resize(1024, 768)
    window.select_page("batch")
    window.show()
    qtbot.waitUntil(lambda: panel.tbl.cellWidget(0, 10).isVisible())
    assert panel.tbl.viewport().rect().contains(panel.tbl.cellWidget(0, 10).geometry())


@pytest.mark.parametrize("source", ["history", "batches"])
def test_click_completes_in_background_and_refreshes_both_views(window, qtbot, source):
    directory = make_history(window)
    panel = window.batch_panel
    panel.api_key = "test"
    record = {"id": "batch_work", "status": "completed", "output_file_id": "file_out"}
    panel._on_refreshed({"key": "test", "batches": [record]})
    client = Mock()
    client.retrieve_batch.return_value = record
    threads = []
    def downloaded(file_id):
        threads.append(threading.get_ident())
        return modify_output(directory)
    client.file_content.side_effect = downloaded
    panel.client = client
    window.history_panel.lst_runs.setCurrentRow(0)
    assert not window.history_panel.btn_rerun.isEnabled()
    with patch("kajovo.desktop.batches.msg_info"), patch.object(panel, "load"):
        action = completion_button(window) if source == "history" else panel.tbl.cellWidget(0, 10)
        action.click()
        assert not completion_button(window).isEnabled()
        qtbot.waitUntil(lambda: panel._operation_task is None, timeout=5000)
    assert read_state(directory)["status"] == "files_complete_unverified"
    assert completion_button(window) is None
    assert panel.tbl.cellWidget(0, 10) is None
    assert "Soubory uloženy" in panel.tbl.item(0, 9).text()
    assert window.history_panel.lst_runs.currentRow() == 0
    assert threads and threads[0] != threading.get_ident()
    client.create_batch.assert_not_called()
    client.create_response.assert_not_called()


def test_double_completion_and_repeat_share_operation_lock(window, qtbot):
    directory = make_history(window)
    panel = window.batch_panel
    panel.api_key = "test"
    panel.client = Mock()
    entered, release = threading.Event(), threading.Event()
    def operation(client):
        entered.set()
        assert release.wait(5)
        return {"status": "batch_pending", "written": []}
    with patch("kajovo.desktop.batches.msg_info"), patch.object(panel, "load"), patch.object(panel.jobs, "start", wraps=panel.jobs.start) as start:
        try:
            panel._start_operation(operation, run_id=directory.name)
            qtbot.waitUntil(entered.is_set)
            panel.complete_run(directory.name)
            panel._start_operation(lambda client: {}, run_id=directory.name)
            assert start.call_count == 1
        finally:
            release.set()
            qtbot.waitUntil(lambda: panel._operation_task is None, timeout=5000)


def test_active_run_blocks_import_to_overlapping_output(window):
    directory = make_history(window)
    window.batch_panel.api_key = "test"
    target = read_state(directory)["out_dir"]
    window._run_contexts["other"] = {"worker": SimpleNamespace(cfg=SimpleNamespace(out_dir=target))}
    try:
        with patch("kajovo.desktop.batches.msg_warning") as warning, patch.object(window.batch_panel.jobs, "start") as start:
            window.batch_panel.complete_run(directory.name)
        start.assert_not_called()
        assert "OUT" in warning.call_args.args[-1]
    finally:
        window._run_contexts.clear()


def test_failed_operation_releases_history_button(window, qtbot):
    from kajovo.desktop.jobs import Job
    directory = make_history(window)
    panel = window.batch_panel
    panel.api_key = "test"
    panel.client = Mock()
    panel.client.retrieve_batch.side_effect = RuntimeError("Síť není dostupná")
    original_start = Job.start

    def finish_before_start_returns(job):
        original_start(job)
        assert job.wait(5000)

    with patch("kajovo.desktop.jobs.msg_warning") as warning, patch.object(Job, "start", finish_before_start_returns):
        panel.complete_run(directory.name)
        qtbot.waitUntil(lambda: panel._operation_task is None, timeout=5000)
    assert warning.called
    assert completion_button(window).isEnabled()
    assert read_state(directory)["status"] == "batch_pending"


def models(window):
    window.all_models = ["gpt-4.1", "gpt-4.1-nano", "whisper-1"]
    window.cb_model.addItems(window.all_models[:2])
    window.settings_page.refresh_models(window.all_models)
    window._refresh_model_tab()


def test_default_model_is_persisted_without_replacing_current_assignment(window):
    models(window)
    page = window.settings_page
    window._set_active_model("gpt-4.1")
    with patch("kajovo.desktop.settings.msg_info"):
        assert page.save_default_model("gpt-4.1-nano")
        assert window.cb_model.currentText() == "gpt-4.1"
        assert page.default_model.currentData() == "gpt-4.1-nano"
        page.save()
    saved = json.loads(Path("kajovo_settings.json").read_text(encoding="utf-8"))
    assert saved["default_model"] == "gpt-4.1-nano"
    window.on_new()
    assert window.cb_model.currentText() == "gpt-4.1-nano"
    window._apply_state({"model": "gpt-4.1", "model_a1": "gpt-4.1"})
    assert window.cb_model.currentText() == "gpt-4.1"
    assert window._get_generate_model_override(window.cb_model_a1) == "gpt-4.1"


def test_default_model_save_failure_and_unavailable_model_preserve_settings(window):
    models(window)
    page = window.settings_page
    with patch("kajovo.desktop.settings.save_settings", side_effect=OSError("Zápis selhal")), patch("kajovo.desktop.settings.msg_warning"):
        assert not page.save_default_model("gpt-4.1")
    assert window.s.default_model == ""
    assert page.default_model.currentData() == ""
    with patch("kajovo.desktop.settings.msg_warning"), patch("kajovo.desktop.settings.save_settings") as save:
        assert not page.save_default_model("whisper-1")
        assert not page.save_default_model("missing-model")
        save.assert_not_called()
    window.s.default_model = "missing-model"
    page.set_model_selection("missing-model")
    page.refresh_models([])
    assert page.default_model.currentData() == "missing-model"
    assert not page.default_model.model().item(page.default_model.currentIndex()).isEnabled()


def test_main_model_default_action_uses_shared_settings(window):
    models(window)
    window._set_active_model("gpt-4.1")
    with patch("kajovo.desktop.settings.msg_info"):
        window.btn_default_model.click()
    assert window.s.default_model == "gpt-4.1"
    assert window.settings_page.default_model.currentData() == "gpt-4.1"
    assert "gpt-4.1" in window.lbl_default_model.text()


def test_saved_default_is_used_after_window_restart(window, qtbot):
    from kajovo.core.config import load_settings
    from kajovo.desktop.application import MainWindow
    models(window)
    with patch("kajovo.desktop.settings.msg_info"):
        window.settings_page.save_default_model("gpt-4.1-nano")
    with patch("kajovo.desktop.application.load_api_key", return_value=""), patch("kajovo.desktop.application.get_secret", return_value=None):
        restored = MainWindow(load_settings())
    qtbot.addWidget(restored)
    assert restored.cb_model.currentText() == "gpt-4.1-nano"
    assert restored.settings_page.default_model.currentData() == "gpt-4.1-nano"
    restored.close()
