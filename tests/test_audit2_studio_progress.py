"""Chybové signály a terminální průběh bez domýšlení dokončených fází."""

from unittest.mock import Mock

import pytest
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from kajovo.core.progress import ProgressEvent
from kajovo.core.user_errors import describe_error
from kajovo.progress_ui import ProcessInspector
from kajovo.studio.operations import Operations


@pytest.mark.parametrize("order", ["simple", "detail_first", "detail_last"])
def test_finished_err_is_observed_and_detail_wins(qtbot, tmp_path, order):
    detail = describe_error(ValueError("Podrobná příčina"))

    class Worker(QThread):
        finished_ok = Signal(object)
        finished_err = Signal(str)
        failure_detail = Signal(object)

        def run(self):
            if order == "detail_first":
                self.failure_detail.emit(detail)
            self.finished_err.emit("Běh používá jiná instance")
            if order == "detail_last":
                self.failure_detail.emit(detail)

    parent = QWidget()
    qtbot.addWidget(parent)
    manager = Operations(parent)
    received = Mock()
    record = manager.adopt("Test", Worker(), received, popup=False, output_dir=tmp_path)
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == "failed"
    received.assert_not_called()
    if order == "simple":
        assert "Běh používá jiná instance" in record.error.detail
    else:
        assert record.error == detail
    manager.assert_output_available(tmp_path)
    record.dialog.close()


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled", "dry_run", "batch_pending", "partial"])
def test_terminal_progress_keeps_only_evidenced_completion(qtbot, terminal):
    inspector = ProcessInspector(kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.on_event(ProgressEvent("A1", "completed"))
    inspector.on_event(ProgressEvent("A2", "active"))
    event = ProgressEvent("RUN", terminal)
    inspector.on_event(event)
    steps = inspector._process_steps()
    assert sum(state == "done" for _, state in steps) == 1
    assert not any(state in {"current", "pending"} for _, state in steps)
    assert not any(state in {"current", "pending", "done"} for _, state in inspector._micro_steps(event))
    assert "Místní operace skončila" in inspector.next_label.text()

