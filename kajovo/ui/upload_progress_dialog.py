from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QProgressBar
from PySide6.QtCore import Qt

from .theme import DARK_STYLESHEET
from .widgets import style_progress_bar, TitleBar, app_icon


class UploadProgressDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._done = False
        self._cancel_handler = None
        self._cancelled = False

        self.setWindowTitle(f"Kájovo NG — {title}")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setModal(True)
        self.resize(720, 420)
        self.setStyleSheet(DARK_STYLESHEET)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        self.titlebar = TitleBar(title, self)
        self.titlebar.request_close.connect(self.reject)
        v.addWidget(self.titlebar)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("font-weight: 600;")
        self.lbl_title.hide()

        self.lbl_status = QLabel("...")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        self.lbl_current = QLabel("")
        self.lbl_current.setWordWrap(True)
        v.addWidget(self.lbl_current)

        self.pb = QProgressBar()
        style_progress_bar(self.pb)
        v.addWidget(self.pb)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._handle_cancel)
        self.btn_close = QPushButton("Close")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_close)
        v.addLayout(row)

    def set_cancel_handler(self, fn):
        self._cancel_handler = fn

    def set_cancel_enabled(self, enabled: bool):
        self.btn_cancel.setEnabled(enabled)

    def set_status(self, text: str):
        self.lbl_status.setText(text)

    def set_current(self, text: str):
        self.lbl_current.setText(text)

    def set_progress(self, value: int):
        self.pb.setValue(value)

    def add_log(self, line: str):
        self.log.appendPlainText(line)

    def mark_done(self, status: str = "Hotovo."):
        self._done = True
        self.btn_cancel.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.set_status(status)

    def _handle_cancel(self):
        if self._cancelled:
            return
        self._cancelled = True
        if self._cancel_handler:
            try:
                self._cancel_handler()
            except Exception:
                pass
        # po zrušení dovolíme dialog zavřít
        self._done = True
        self.btn_close.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.set_status("Ruším...")

    def reject(self):
        # ESC nebo zavření dialogu se chová jako cancel
        if not self._done:
            self._handle_cancel()
        super().reject()

    def closeEvent(self, event):
        if not self._done:
            # zavření křížkem = okamžité zrušení
            self._handle_cancel()
        super().closeEvent(event)
