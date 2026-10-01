"""Ownership a GUI vlákno při dokončení QThread ve vnořeném dialogu."""
import threading

from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from kajovo.studio.file_dialogs import FileDialog
from kajovo.studio.operations import Operations


def test_worker_cleanup_inside_repeated_file_dialog_event_loops(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    manager = Operations(parent)
    observations = []
    for index in range(3):
        entered, release = threading.Event(), threading.Event()
        def work(_task, entered=entered, release=release):
            assert QThread.currentThread() != QApplication.instance().thread()
            entered.set()
            assert release.wait(10)
            return {'status': 'completed', 'iteration': len(observations)}
        def receive(value):
            observations.append((value, QThread.currentThread() == QApplication.instance().thread()))
        record = manager.start('Vnořený dialog', work, receive, popup=False)
        qtbot.addWidget(record.dialog)
        qtbot.waitUntil(entered.is_set, timeout=10000)
        dialog = FileDialog(parent, 'Výběr po QA')
        qtbot.addWidget(dialog)
        assert dialog.parent() is parent
        timer = QTimer(dialog)
        def inspect_completion(record=record, dialog=dialog):
            if record.terminal:
                assert not manager.active
                assert record.terminal == 'completed'
                dialog.reject()
        timer.timeout.connect(inspect_completion)
        timer.start(10)
        QTimer.singleShot(0, release.set)
        assert dialog.exec() == FileDialog.Rejected
        timer.stop()
        assert isValid(dialog) and isValid(record.dialog)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qtbot.waitUntil(lambda record=record: not isValid(record.worker), timeout=10000)
        assert observations[index][0]['iteration'] == index
        assert observations[index][1] is True
        record.dialog.close()
    assert len(observations) == 3
