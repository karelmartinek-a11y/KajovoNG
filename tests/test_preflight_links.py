"""Zkušební dávka musí zůstat propojená s běhy a nesmí vstoupit do importu."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QPushButton

import test_desktop
from test_generate_batch import manifest
from kajovo.core.batch_completion import (
    local_batches, batch_ids, pending_batch_ids, can_continue_preflight,
    read_state, read_batch_statuses, save_batch_statuses,
)
from kajovo.core.runlog import RunLogger
from kajovo.desktop.batch_view import saved_record

window = test_desktop.window


def saved_run(log_dir, suffix="ABCD"):
    directory = Path(log_dir) / ("RUN_110920261449_" + suffix)
    directory.mkdir(parents=True)
    state = {"run_id": directory.name, "project": "NO_PROJECT", "status": "preflight_pending",
             "preflight_batches": [{"id": "batch_trial", "status": "validating"}],
             "generate_batch": manifest(), "ui_state": {"mode": "GENERATE", "send_as_c": True}}
    (directory / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    return directory


def history_button(window):
    item = window.history_panel.lst_runs.item(0)
    return window.history_panel.lst_runs.itemWidget(item).findChild(QPushButton)


def test_legacy_trial_link_and_shared_runs(tmp_path):
    first = saved_run(tmp_path)
    saved_run(tmp_path, "EFGH")
    info = local_batches(tmp_path)["batch_trial"]
    assert len(info["runs"]) == 2
    assert info["kind"] == "preflight"
    state = read_state(first)
    assert batch_ids(state) == pending_batch_ids(state) == []
    assert can_continue_preflight(state)
    state["batch_id"] = "batch_work"
    assert not can_continue_preflight(state)


@pytest.mark.parametrize("status", ["validating", "in_progress", "completed", "failed", "cancelled", "expired"])
def test_refresh_updates_both_views_without_changing_run(window, status):
    directory = saved_run(window.s.log_dir)
    before = (directory / "run_state.json").read_bytes()
    panel = window.batch_panel
    panel._on_refreshed({"key": panel.api_key, "batches": [
        {"id": "batch_trial", "status": status, "created_at": 1726050000},
    ]})
    assert panel.tbl.item(0, 8).text() == "Bez projektu"
    assert panel.tbl.item(0, 11).text() == "Zkušební"
    assert panel.tbl.item(0, 12).text() == directory.name
    assert panel.tbl.item(0, 9).text() == "Nevytváří soubory"
    assert panel.tbl.cellWidget(0, 10).text() == history_button(window).text() == "Pokračovat"
    title = window.history_panel.lst_runs.item(0).text()
    assert "batch_trial" in title
    if status == "completed":
        assert "čeká na převzetí ověření" in title
    else:
        assert panel.tbl.item(0, 1).text() in title
    assert (directory / "run_state.json").read_bytes() == before
    assert read_batch_statuses(window.s.log_dir)["batch_trial"]["checked_at"] > 0
    panel.tbl.selectRow(0)
    assert not panel.btn_download.isEnabled()
    assert not panel.btn_repeat.isEnabled()
    assert not panel.btn_repair.isEnabled()
    with patch("kajovo.desktop.batches.msg_info"), patch.object(panel, "_start_operation") as operation:
        panel.download()
        panel.repeat_selected()
    operation.assert_not_called()


@pytest.mark.parametrize("source", ["history", "batches"])
def test_continue_routes_to_same_saved_manifest(window, source):
    directory = saved_run(window.s.log_dir)
    window._refresh_batch_links()
    panel = window.batch_panel
    seen = []
    with patch("kajovo.desktop.application.recover_run", return_value=({}, None, [])), \
         patch.object(window, "_apply_state"), \
         patch.object(window, "on_go", side_effect=lambda: seen.append(window._resume_batch_state)):
        action = history_button(window) if source == "history" else panel.tbl.cellWidget(0, 10)
        action.click()
    assert seen == [read_state(directory)]


def test_shared_trial_menu_and_work_submission_preserve_links(window):
    first = saved_run(window.s.log_dir)
    second = saved_run(window.s.log_dir, "EFGH")
    window._refresh_batch_links()
    action = window.batch_panel.tbl.cellWidget(0, 10)
    assert len(action.menu().actions()) == 2
    assert all(any(path.name in entry.text() for entry in action.menu().actions()) for path in (first, second))
    for path in (first, second):
        state = read_state(path)
        state.update(batch_id="batch_work_" + path.name, status="batch_pending")
        (path / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    window._refresh_batch_links()
    panel = window.batch_panel
    for index in range(panel.tbl.rowCount()):
        if panel.tbl.item(index, 0).text() == "batch_trial":
            assert panel.tbl.cellWidget(index, 10) is None
            assert first.name in panel.tbl.item(index, 12).text()


def test_active_run_blocks_both_buttons_and_direct_continue(window):
    directory = saved_run(window.s.log_dir)
    window._run_contexts[directory.name] = {"worker": Mock()}
    try:
        window._refresh_batch_links()
        assert not history_button(window).isEnabled()
        assert not window.batch_panel.tbl.cellWidget(0, 10).isEnabled()
        with patch("kajovo.desktop.batches.msg_warning") as warning, patch.object(window, "on_go") as start:
            window.batch_panel.continue_run(directory.name)
        warning.assert_called_once()
        start.assert_not_called()
    finally:
        window._run_contexts.clear()


def test_snapshot_merge_preserves_date_and_latest_local_observation(tmp_path):
    save_batch_statuses(tmp_path, [{"id": "batch_trial", "created_at": 1726050000, "status": "validating"}])
    save_batch_statuses(tmp_path, [{"id": "batch_trial", "status": "completed"}])
    snapshots = read_batch_statuses(tmp_path)
    assert snapshots["batch_trial"]["created_at"] == 1726050000
    state = {"preflight_batches": [{"id": "batch_trial", "status": "failed",
                                    "checked_at": snapshots["batch_trial"]["checked_at"] + 1}]}
    assert saved_record(state, "batch_trial", snapshots)["status"] == "failed"


def test_observer_preserves_trial_after_work_submission(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_110920261449_ABCD")
    logger.record_preflight_batch({"id": "batch_trial", "created_at": 123, "status": "validating"})
    logger.record_preflight_batch({"id": "batch_trial", "status": "completed"})
    logger.update_state({"batch_id": "batch_work", "status": "batch_pending"})
    state = read_state(logger.paths.run_dir)
    assert state["preflight_batches"] == [{"id": "batch_trial", "created_at": 123, "status": "completed"}]
    assert set(local_batches(tmp_path)) == {"batch_trial", "batch_work"}


def test_refresh_failure_keeps_last_known_state(window, qtbot):
    saved_run(window.s.log_dir)
    panel = window.batch_panel
    panel.api_key = "test"
    panel._on_refreshed({"key": "test", "batches": [{"id": "batch_trial", "status": "completed"}]})
    before = read_batch_statuses(window.s.log_dir)
    panel.client = Mock()
    panel.client.list_batches.side_effect = RuntimeError("Síť není dostupná")
    with patch("kajovo.desktop.jobs.msg_warning") as warning:
        panel.load()
        qtbot.waitUntil(lambda: panel._refresh_task is None, timeout=5000)
    warning.assert_called_once()
    assert read_batch_statuses(window.s.log_dir) == before
    assert "čeká na převzetí ověření" in window.history_panel.lst_runs.item(0).text()
