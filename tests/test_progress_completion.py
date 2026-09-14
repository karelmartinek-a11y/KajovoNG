"""Dokončení průběhu umožňuje zavření bez rušení práce."""

import threading
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from kajovo.core.progress import ProgressEvent
from kajovo.core.user_errors import describe_error
from kajovo.desktop.dialogs import ProgressDialog, TaskProgressDialog, UploadProgressDialog
from kajovo.studio.operations import OperationDialog, Operations, STATES


@pytest.mark.parametrize("state", sorted(set(STATES) - {"active", "waiting"}))
@pytest.mark.parametrize("enter", [False, True])
def test_studio_terminal_can_close_without_cancelling(qtbot, state, enter):
    dialog = OperationDialog("Práce")
    qtbot.addWidget(dialog)
    stop = Mock()
    dialog.stop_callback = stop
    dialog.stop.setEnabled(True)
    dialog.show()
    dialog.resize(480, 360)
    dialog.on_event(ProgressEvent("Soubory", completed=3, total=8))
    dialog.result = {"status": state}
    error = describe_error(ValueError("Chyba")) if state == "failed" else None
    dialog.finish(state, error)
    assert dialog.isVisible()
    assert dialog.stop.isHidden()
    assert dialog.close_button.text() == "OK"
    assert dialog.close_button.accessibleName() == "OK"
    assert dialog.close_button.isDefault()
    assert dialog.result_button.isVisible()
    assert dialog.details.isVisible() == (error is not None)
    assert dialog.progress.value() == 3
    assert not dialog.timer.isActive()
    assert not dialog.mark.running
    qtbot.wait(10)
    point = dialog.close_button.mapTo(dialog, dialog.close_button.rect().center())
    assert dialog.rect().contains(point)
    if enter:
        qtbot.keyClick(dialog.close_button, Qt.Key_Return)
    else:
        qtbot.mouseClick(dialog.close_button, Qt.LeftButton)
    assert not dialog.isVisible()
    dialog.show()
    assert dialog.close_button.text() == "OK"
    stop.assert_not_called()


def test_studio_completion_waits_and_reuse_restores_controls(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    operations = Operations(parent)
    previous = None
    for attempt in range(2):
        release = threading.Event()

        def execute(task, release=release):
            task.progress_event.emit(ProgressEvent("Soubory", completed=1, total=1))
            task.progress_event.emit(ProgressEvent("RUN", "completed"))
            release.wait(5)
            return {"status": "completed"}

        record = operations.start("Zápis", execute, cancellable=True, identifier="opakování")
        try:
            qtbot.waitUntil(lambda record=record: len(record.events) == 2)
            dialog = record.dialog
            assert dialog.active
            assert dialog.close_button.text() == "Skrýt průběh"
            assert not dialog.close_button.isDefault()
            assert dialog.stop.isVisible()
            assert dialog.stop.isEnabled()
            if attempt:
                assert dialog is previous
            dialog.request_stop()
            assert not dialog.stop.isEnabled()
            assert dialog.close_button.text() == "Skrýt průběh"
            dialog.hide()
        finally:
            release.set()
            qtbot.waitUntil(lambda record=record: bool(record.terminal))
        assert not dialog.isVisible()
        assert dialog.close_button.text() == "OK"
        previous = dialog


@pytest.mark.parametrize("dialog_type", [TaskProgressDialog, UploadProgressDialog])
@pytest.mark.parametrize("state", ["success", "failed", "cancelled"])
@pytest.mark.parametrize("enter", [False, True])
def test_task_completion_hides_cancel_and_offers_ok(qtbot, dialog_type, state, enter):
    dialog = dialog_type("Operace")
    qtbot.addWidget(dialog)
    cancel = Mock()
    dialog.set_cancel_handler(cancel)
    dialog.pb_sub.setRange(0, 0)
    dialog.show()
    getattr(dialog, "mark_" + state)()
    assert dialog.btn_cancel.isHidden()
    assert dialog.btn_close.text() == "OK"
    assert dialog.btn_close.isEnabled()
    assert dialog.pb.maximum() > 0
    assert dialog.pb_sub.maximum() > 0
    assert not dialog.timer.isActive()
    if enter:
        qtbot.keyClick(dialog.btn_close, Qt.Key_Return)
    else:
        qtbot.mouseClick(dialog.btn_close, Qt.LeftButton)
    assert not dialog.isVisible()
    cancel.assert_not_called()


def test_legacy_unverified_files_offer_ok(qtbot):
    dialog = ProgressDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.on_progress_event(ProgressEvent("RUN", "files_complete_unverified"))
    assert dialog.btn_stop.isHidden()
    assert dialog.btn_cancel_response.isHidden()
    assert dialog.btn_close.text() == "OK"
    assert not dialog.timer.isActive()
    assert dialog.pb.value() == 0
