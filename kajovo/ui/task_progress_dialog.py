from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QProgressBar

from .theme import DARK_STYLESHEET
from .widgets import style_progress_bar


class TaskProgressDialog(QDialog):
    minimize_requested = Signal()

    def __init__(self, title: str, parent=None, *, show_subprogress: bool = True):
        super().__init__(parent)
        self._done = False
        self._start = time.time()
        self._cancel_handler = None
        self._cancelled = False

        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(640, 360)
        self.setStyleSheet(DARK_STYLESHEET)

        v = QVBoxLayout(self)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("font-weight: 600;")
        v.addWidget(self.lbl_title)

        self.lbl_status = QLabel("...")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        hdr = QHBoxLayout()
        self.lbl_step = QLabel("Krok: 1/1")
        self.lbl_step.setVisible(False)
        self.lbl_ai = QLabel("AI ●")
        self.lbl_ai.setToolTip("Červeně: probíhá komunikace s OpenAI. Bílá: neaktivní.")
        hdr.addWidget(self.lbl_step)
        hdr.addStretch(1)
        hdr.addWidget(self.lbl_ai)
        v.addLayout(hdr)

        self.pb = QProgressBar()
        style_progress_bar(self.pb)
        v.addWidget(self.pb)

        self.pb_sub = QProgressBar()
        style_progress_bar(self.pb_sub)
        v.addWidget(self.pb_sub)
        if not show_subprogress:
            self.pb_sub.hide()

        info = QHBoxLayout()
        self.lbl_elapsed = QLabel("Uplynulo: 0 s")
        self.lbl_eta = QLabel("Odhad konce: —")
        self.lbl_remaining = QLabel("Zbývá: —")
        info.addWidget(self.lbl_elapsed)
        info.addWidget(self.lbl_eta)
        info.addWidget(self.lbl_remaining)
        info.addStretch(1)
        v.addLayout(info)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.btn_stop = QPushButton("Zastavit")
        self.btn_minimize = QPushButton("Minimalizovat")
        self.btn_close = QPushButton("Zavřít")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        self.btn_stop.clicked.connect(self._handle_cancel)
        self.btn_minimize.clicked.connect(self._request_minimize)
        row.addStretch(1)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_minimize)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(400)
        self._ui_timer.timeout.connect(self._update_time_metrics)
        self._ui_timer.start()
        self._set_ai_color(False)

    def set_cancel_handler(self, fn):
        self._cancel_handler = fn

    def set_step_info(self, current: int, total: int):
        total_safe = max(1, int(total))
        cur_safe = max(1, min(total_safe, int(current)))
        self.lbl_step.setVisible(total_safe > 1)
        self.lbl_step.setText(f"Krok: {cur_safe}/{total_safe}")

    def set_ai_busy(self, busy: bool):
        self._set_ai_color(bool(busy))

    def _set_ai_color(self, busy: bool):
        if busy:
            self.lbl_ai.setStyleSheet("color: #ff3b30; font-weight: 700;")
        else:
            self.lbl_ai.setStyleSheet("color: rgba(255,255,255,0.45); font-weight: 600;")

    def set_indeterminate(self, enabled: bool):
        if enabled:
            self.pb.setRange(0, 0)
        else:
            self.pb.setRange(0, 100)

    def set_status(self, text: str):
        self.lbl_status.setText(text)

    def set_progress(self, value: int):
        self.pb.setValue(value)
        self._update_eta()

    def set_subprogress(self, value: int):
        if self.pb_sub.isHidden():
            return
        self.pb_sub.setValue(value)

    def add_log(self, line: str):
        self.log.appendPlainText(line)

    def mark_done(self, status: str = "Hotovo."):
        self._done = True
        self.btn_stop.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.set_status(status)

    def _request_minimize(self):
        self.showMinimized()
        self.minimize_requested.emit()

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

    def _handle_cancel(self):
        if self._cancelled:
            return
        self._cancelled = True
        if self._cancel_handler:
            try:
                self._cancel_handler()
            except Exception:
                pass
        self._done = True
        self.btn_stop.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.set_status("Ruším...")

    def closeEvent(self, event):
        if not self._done:
            self._handle_cancel()
        super().closeEvent(event)
