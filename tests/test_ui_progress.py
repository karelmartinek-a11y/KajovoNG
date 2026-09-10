import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer
from kajovo.core.progress import ProgressClock, ProgressEvent
from kajovo.ui.background import run_io
from kajovo.ui.progress_dialog import ProgressDialog
from kajovo.ui.upload_progress_dialog import UploadProgressDialog
from test_workflows import make_worker


def test_eta_requires_completed_samples_and_counts_down():
    clock = ProgressClock(now=0)
    clock.update(ProgressEvent("A3", completed=0, total=6, timestamp=0))
    for i in range(1, 3):
        clock.update(ProgressEvent("A3", completed=i, total=6, timestamp=i * 10))
        assert clock.times(i * 10)[2] is None
    clock.update(ProgressEvent("A3", completed=3, total=6, timestamp=30))
    assert clock.times(30)[2] == 30
    assert clock.times(35)[2] == 25
    clock.update(ProgressEvent("Ukládání", completed=0, total=6, timestamp=40))
    assert clock.times(40)[2] is None


def test_approval_does_not_inflate_unit_duration():
    clock = ProgressClock(now=0)
    clock.update(ProgressEvent("A3", completed=0, total=5, timestamp=0))
    clock.update(ProgressEvent("A3", "approval", timestamp=5))
    clock.update(ProgressEvent("A3", "active", timestamp=105))
    clock.update(ProgressEvent("A3", completed=1, total=5, timestamp=110))
    assert list(clock.samples) == [10]


def test_repeated_poll_does_not_restart_unit_measurement():
    clock = ProgressClock(now=0)
    clock.update(ProgressEvent("Indexace", completed=0, total=5, timestamp=0))
    clock.update(ProgressEvent("Indexace", completed=0, total=5, timestamp=8))
    clock.update(ProgressEvent("Indexace", completed=1, total=5, timestamp=10))
    assert list(clock.samples) == [10]


def test_palette_text_and_selection_contrast(qapp):
    from PySide6.QtGui import QPalette
    from kajovo.ui.layouts import install_ui_style
    install_ui_style()
    palette = qapp.palette()

    def luminance(color):
        values = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722), strict=True))

    for foreground, background in ((QPalette.Text, QPalette.Base),
                                   (QPalette.PlaceholderText, QPalette.Base),
                                   (QPalette.HighlightedText, QPalette.Highlight)):
        a, b = sorted((luminance(palette.color(foreground)), luminance(palette.color(background))))
        assert (b + .05) / (a + .05) >= 4.5


def test_copy_keeps_full_value_and_zero(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
    from PySide6.QtCore import Qt
    from kajovo.ui.layouts import copy_selection
    table = QTableWidget(1, 2)
    qtbot.addWidget(table)
    path = "složka/" * 100 + "soubor.py"
    table.setItem(0, 0, QTableWidgetItem(path))
    zero = QTableWidgetItem()
    zero.setData(Qt.DisplayRole, 0)
    table.setItem(0, 1, zero)
    table.selectRow(0)
    clipboard = Mock()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    copy_selection(table)
    clipboard.setText.assert_called_once_with(path + "\t0")


@pytest.mark.parametrize(
    "state,value", [("batch_pending", 0), ("completed", 100), ("failed", 0), ("cancelled", 0)]
)
def test_terminal_state_is_not_inferred_from_percent(qtbot, state, value):
    dialog = ProgressDialog()
    qtbot.addWidget(dialog)
    dialog.set_progress(100)
    assert dialog.pb.maximum() == 0
    dialog.on_progress_event(ProgressEvent("RUN", state))
    assert dialog.pb.value() == value
    assert dialog.pb_sub.maximum() == 100
    assert not dialog.timer.isActive()
    assert not dialog.btn_stop.isEnabled()


def test_upload_cancel_waits_for_worker_acknowledgement(qtbot):
    dialog = UploadProgressDialog("Nahrávání")
    qtbot.addWidget(dialog)
    cancel = Mock()
    dialog.set_cancel_handler(cancel)
    dialog.show()
    dialog.reject()
    assert dialog.isVisible()
    assert not dialog._done
    assert not dialog.btn_close.isEnabled()
    dialog.reject()
    cancel.assert_called_once()
    dialog.mark_done("Zrušeno.")
    dialog.reject()
    assert not dialog.isVisible()


def test_io_runs_outside_gui_while_timer_remains_responsive(qapp):
    main_thread = threading.get_ident()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()

    def operation():
        time.sleep(0.05)
        return threading.get_ident()

    try:
        assert run_io(operation) != main_thread
        assert ticks
        with pytest.raises(ValueError, match="fixture"):
            run_io(lambda: (_ for _ in ()).throw(ValueError("fixture")))
    finally:
        timer.stop()


@pytest.mark.parametrize("fail", [False, True])
def test_file_is_counted_only_after_generation(tmp_path, monkeypatch, fail):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.resume_files = [{"path": "hello.py", "purpose": "test"}]
    worker.cfg.resume_prev_id = "resp_previous"
    events = []
    worker.progress_event.connect(events.append)

    def generate(*args, **kwargs):
        assert [e.completed for e in events if e.stage == "A3" and e.completed is not None] == [0]
        if fail:
            raise ValueError("fixture")
        return "print('hello')\n", "resp_test"

    monkeypatch.setattr(worker, "_gen_file_chunks", generate)
    if fail:
        with pytest.raises(ValueError, match="fixture"):
            worker._run_a_generate(Mock(), [], None)
    else:
        worker._run_a_generate(Mock(), [], None)
    assert [e.completed for e in events if e.stage == "A3" and e.completed is not None] == (
        [0] if fail else [0, 1]
    )
