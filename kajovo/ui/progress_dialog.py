from __future__ import annotations

import time
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from .widgets import style_progress_bar
from .theme import DARK_STYLESHEET

class ProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RUN")
        self.setModal(False)
        self.resize(720, 520)
        self.setStyleSheet(DARK_STYLESHEET)
        self._start = time.time()

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        self.lbl = QLabel("Running...")
        v.addWidget(self.lbl)

        self.pb = QProgressBar()
        self.pb_sub = QProgressBar()
        style_progress_bar(self.pb)
        style_progress_bar(self.pb_sub)
        v.addWidget(self.pb)
        v.addWidget(self.pb_sub)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.btn_stop = QPushButton("STOP")
        self.chk_bzz = QCheckBox("BZZonEND")
        self.btn_close = QPushButton("Hide")
        row.addWidget(self.btn_stop)
        row.addWidget(self.chk_bzz)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        self.btn_close.clicked.connect(self.hide)
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(10000)
        self._dot_timer.timeout.connect(self._pulse_log_line)
        self._dot_timer.start()

    def set_progress(self, p: int):
        self.pb.setValue(p)
        self._update_eta()

    def set_subprogress(self, p: int):
        self.pb_sub.setValue(p)

    def set_status(self, s: str):
        self.lbl.setText(s)
        self._update_eta()

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

    def _update_eta(self):
        p = self.pb.value()
        if p <= 1:
            return
        elapsed = time.time() - self._start
        total = elapsed * (100.0 / p)
        eta = max(0.0, total - elapsed)
        self.setWindowTitle(f"RUN — ETA {int(eta)}s")
