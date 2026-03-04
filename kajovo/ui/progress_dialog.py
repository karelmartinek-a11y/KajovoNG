from __future__ import annotations

import time
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import DARK_STYLESHEET
from .widgets import style_progress_bar


class ProgressDialog(QDialog):
    minimize_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RUN")
        self.setModal(False)
        self.resize(760, 560)
        self.setStyleSheet(DARK_STYLESHEET)
        self._start = time.time()
        self._ai_busy = False

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        hdr = QHBoxLayout()
        self.lbl = QLabel("Spouštím operaci...")
        hdr.addWidget(self.lbl, 1)

        self.lbl_ai = QLabel("AI ●")
        self.lbl_ai.setToolTip("Červeně: probíhá komunikace s OpenAI. Bílá: neaktivní.")
        hdr.addWidget(self.lbl_ai)
        v.addLayout(hdr)

        self.lbl_step = QLabel("Krok: 1/1")
        self.lbl_step.setVisible(False)
        v.addWidget(self.lbl_step)

        self.pb = QProgressBar()
        self.pb_sub = QProgressBar()
        style_progress_bar(self.pb)
        style_progress_bar(self.pb_sub)
        v.addWidget(self.pb)
        v.addWidget(self.pb_sub)

        info = QHBoxLayout()
        self.lbl_elapsed = QLabel("Uplynulo: 0 s")
        self.lbl_eta = QLabel("Odhad konce: —")
        self.lbl_remaining = QLabel("Zbývá: 100 %")
        info.addWidget(self.lbl_elapsed)
        info.addWidget(self.lbl_eta)
        info.addWidget(self.lbl_remaining)
        info.addStretch(1)
        v.addLayout(info)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.btn_stop = QPushButton("STOP")
        self.chk_bzz = QCheckBox("BZZ po dokončení")
        self.btn_minimize = QPushButton("Minimalizovat")
        self.btn_close = QPushButton("Skrýt")
        row.addWidget(self.btn_stop)
        row.addWidget(self.chk_bzz)
        row.addStretch(1)
        row.addWidget(self.btn_minimize)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        self.btn_close.clicked.connect(self.hide)
        self.btn_minimize.clicked.connect(self._request_minimize)
        self.btn_stop.clicked.connect(self.cancel_requested.emit)

        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(10000)
        self._dot_timer.timeout.connect(self._pulse_log_line)
        self._dot_timer.start()

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(400)
        self._ui_timer.timeout.connect(self._update_time_metrics)
        self._ui_timer.start()
        self._set_ai_color(False)

    def _request_minimize(self):
        self.hide()
        self.minimize_requested.emit()

    def set_progress(self, p: int):
        self.pb.setRange(0, 100)
        self.pb.setValue(max(0, min(100, int(p))))
        self._update_eta()

    def set_indeterminate(self, enabled: bool):
        if enabled:
            self.pb.setRange(0, 0)
        else:
            self.pb.setRange(0, 100)

    def set_subprogress(self, p: int):
        self.pb_sub.setValue(max(0, min(100, int(p))))

    def set_status(self, s: str):
        self.lbl.setText(s)
        self._update_eta()

    def set_step_info(self, current: int, total: int):
        total_safe = max(1, int(total))
        cur_safe = max(1, min(total_safe, int(current)))
        self.lbl_step.setVisible(total_safe > 1)
        self.lbl_step.setText(f"Krok: {cur_safe}/{total_safe}")

    def set_ai_busy(self, busy: bool):
        self._ai_busy = bool(busy)
        self._set_ai_color(self._ai_busy)

    def _set_ai_color(self, busy: bool):
        if busy:
            self.lbl_ai.setStyleSheet("color: #ff3b30; font-weight: 700;")
        else:
            self.lbl_ai.setStyleSheet("color: rgba(255,255,255,0.45); font-weight: 600;")

    def add_log(self, line: str):
        self.log.appendPlainText(line)
        self._pulse_log_line(reset=True)

    def _pulse_log_line(self, reset: bool = False):
        doc = self.log.document()
        if doc.blockCount() == 0:
            return
        if reset:
            return
        scroll = self.log.verticalScrollBar()
        at_bottom = scroll.value() >= scroll.maximum()
        block = doc.lastBlock()
        if not block.isValid():
            return
        text = block.text()
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.insertText(text + ".")
        if at_bottom:
            scroll.setValue(scroll.maximum())

    def _update_time_metrics(self):
        elapsed = max(0, int(time.time() - self._start))
        self.lbl_elapsed.setText(f"Uplynulo: {elapsed} s")

    def _update_eta(self):
        p = self.pb.value()
        if p <= 0 or self.pb.maximum() == 0:
            self.lbl_eta.setText("Odhad konce: počítám...")
            self.lbl_remaining.setText("Zbývá: —")
            return
        elapsed = time.time() - self._start
        total = elapsed * (100.0 / max(1, p))
        eta = max(0.0, total - elapsed)
        self.lbl_eta.setText(f"Odhad konce: {int(eta)} s")
        self.lbl_remaining.setText(f"Zbývá: {max(0, 100 - p)} %")
        self.setWindowTitle(f"RUN — ETA {int(eta)}s")

    def closeEvent(self, event):
        if self.isVisible():
            self.cancel_requested.emit()
        super().closeEvent(event)


class MinimizedProgressWidget(QWidget):
    restore_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spinner_frames = ["◐", "◓", "◑", "◒"]
        self._spinner_index = 0
        self._determinate = False
        self._progress = 0

        self.setObjectName("ProgressMiniWidget")
        self.setStyleSheet(
            "#ProgressMiniWidget {border: 1px solid #4a4a4a; border-radius: 8px; padding: 4px; background: rgba(255,255,255,0.05);}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        self.lbl_spinner = QLabel("◐")
        self.lbl_text = QLabel("RUN")
        self.lbl_text.setMaximumWidth(280)
        self.lbl_percent = QLabel("0 %")
        lay.addWidget(self.lbl_spinner)
        lay.addWidget(self.lbl_text)
        lay.addWidget(self.lbl_percent)

        self._timer = QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def mousePressEvent(self, event):
        self.restore_requested.emit()
        super().mousePressEvent(event)

    def set_summary(self, text: str, progress: int, determinate: bool = True):
        self._progress = max(0, min(100, int(progress)))
        self._determinate = bool(determinate)
        self.lbl_text.setText(text)
        self.lbl_percent.setText(f"{self._progress} %" if self._determinate else "∞")

    def _tick(self):
        if self._determinate:
            frame_idx = min(len(self._spinner_frames) - 1, int((self._progress / 100) * (len(self._spinner_frames) - 1)))
            self.lbl_spinner.setText(self._spinner_frames[frame_idx])
        else:
            self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
            self.lbl_spinner.setText(self._spinner_frames[self._spinner_index])
