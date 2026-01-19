from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QProgressBar

from .theme import DARK_STYLESHEET
from .widgets import style_progress_bar


class TaskProgressDialog(QDialog):
    def __init__(self, title: str, parent=None, *, show_subprogress: bool = True):
        super().__init__(parent)
        self._done = False

        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(640, 360)
        self.setStyleSheet(DARK_STYLESHEET)

        v = QVBoxLayout(self)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("font-weight: 600;")
        v.addWidget(self.lbl_title)

        self.lbl_status = QLabel("...")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        self.pb = QProgressBar()
        style_progress_bar(self.pb)
        v.addWidget(self.pb)

        self.pb_sub = QProgressBar()
        style_progress_bar(self.pb_sub)
        v.addWidget(self.pb_sub)
        if not show_subprogress:
            self.pb_sub.hide()

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.btn_close = QPushButton("Close")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        v.addLayout(row)

    def set_status(self, text: str):
        self.lbl_status.setText(text)

    def set_progress(self, value: int):
        self.pb.setValue(value)

    def set_subprogress(self, value: int):
        if self.pb_sub.isHidden():
            return
        self.pb_sub.setValue(value)

    def add_log(self, line: str):
        self.log.appendPlainText(line)

    def mark_done(self, status: str = "Hotovo."):
        self._done = True
        self.btn_close.setEnabled(True)
        self.set_status(status)

    def closeEvent(self, event):
        if not self._done:
            event.ignore()
            return
        super().closeEvent(event)
