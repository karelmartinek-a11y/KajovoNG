"""Průběh a chyby přijímače přes skutečný QThread a GUI event loop."""
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QWidget

from kajovo.core.response_journal import ResponseCancelled, ResponsePending, SubmissionUnknown
from kajovo.studio.operations import Operations


@pytest.fixture
def manager(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    operations = Operations(parent)
    yield operations
    qtbot.waitUntil(lambda: not operations.active, timeout=10000)
    for record in operations.records.values():
        if record.dialog:
            qtbot.addWidget(record.dialog)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("error,state", [
    (ResponsePending("processing"), "response_pending"),
    (SubmissionUnknown("dispatch uncertain"), "submission_unknown"),
    (ResponseCancelled("remote cancelled"), "cancelled"),
    (ValueError("malformed evidence"), "failed"),
])
def test_real_worker_error_keeps_terminal_meaning(manager, qtbot, error, state):
    received = []
    def work(_task):
        raise error
    record = manager.start("Obnova", work, received.append, popup=False)
    qtbot.waitUntil(lambda: bool(record.terminal), timeout=10000)
    assert record.terminal == state
    assert record.error is not None
    assert not received
    assert not record.result_received
    assert record.events[-1].state == state
    assert not manager.active


def test_stop_at_backend_barrier_skips_receive_and_releases_output(manager, qtbot, tmp_path):
    entered, release = threading.Event(), threading.Event()
    received = []
    def work(task):
        entered.set()
        assert release.wait(5)
        task.check_stop()
        return {"status": "completed"}
    record = manager.start("Výroba", work, received.append, popup=False,
                           cancellable=True, output_dir=tmp_path)
    try:
        qtbot.waitUntil(entered.is_set, timeout=10000)
        with pytest.raises(ValueError, match="zapisuje"):
            manager.assert_output_available(tmp_path / "nested")
        record.worker.request_stop()
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(record.terminal), timeout=10000)
    assert record.terminal == "cancelled"
    assert not received and not record.result_received
    manager.assert_output_available(tmp_path)
    assert record.events[-1].stage == "RUN"
    assert record.events[-1].state == "cancelled"


def test_receive_failure_is_visible_after_worker_success(manager, qtbot):
    received = []
    def receive(value):
        received.append(value)
        raise OSError("output unavailable")
    record = manager.start("Převzetí", lambda _: {"status": "completed"}, receive, popup=False)
    qtbot.waitUntil(lambda: bool(record.terminal), timeout=10000)
    assert received == [{"status": "completed"}]
    assert record.result_received and record.terminal == "failed"
    assert record.error is not None
    assert record.events[-1].stage == "Převzetí výsledku"
    assert record.events[-1].state == "failed"
    assert record.dialog.result == record.result


def test_read_receive_failure_removes_record_and_shows_error_dialog(manager, qtbot):
    from kajovo.studio.progress_dialog import MultiProgressDialog
    results = []
    def receive(value):
        results.append(value)
        raise ValueError("invalid local view")
    completed = []
    manager.completed.connect(lambda key, result: completed.append((key, result)))
    record = manager.start_read("Čtení", lambda _: {"rows": [1]}, receive, identifier="local")
    qtbot.waitUntil(lambda: bool(completed), timeout=10000)
    assert results == [{"rows": [1]}]
    assert completed == [("local", {"rows": [1]})]
    assert record.error and record.terminal == "failed"
    assert not manager.records and record.result is None
    dialogs = manager.parent().findChildren(MultiProgressDialog)
    assert len(dialogs) == 1 and dialogs[0].isVisible()
    qtbot.addWidget(dialogs[0])
    dialogs[0].close()


def test_read_overview_replays_events_and_closes_after_receive(manager, qtbot):
    entered, release = threading.Event(), threading.Event()
    received = []
    def work(_task):
        entered.set()
        assert release.wait(5)
        return {"rows": [1]}
    record = manager.start_read("Čtení", work, received.append, identifier="local")
    try:
        qtbot.waitUntil(entered.is_set, timeout=10000)
        assert manager.start_read("Dvojklik", work, identifier="local") is record
        manager.show_all()
        qtbot.addWidget(manager.overview)
        assert manager.listing.count() == 1
        manager.open_selected()
        qtbot.addWidget(record.dialog)
        assert record.dialog.isVisible() and record.events
    finally:
        release.set()
    qtbot.waitUntil(lambda: not manager.records, timeout=10000)
    assert received == [{"rows": [1]}]
    assert record.terminal == "completed"
    assert manager.listing.count() == 0
