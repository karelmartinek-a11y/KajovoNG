from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QDialog,
    QVBoxLayout,
    QProgressBar,
    QMessageBox,
    QPlainTextEdit,
    QFileDialog,
    QInputDialog,
)
from PySide6.QtCore import Qt, QPoint, Signal, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import QApplication
from .theme import DARK_STYLESHEET


class TitleBar(QWidget):
    request_close = Signal()
    request_minimize = Signal()
    request_toggle_max = Signal()

    def __init__(self, title: str = "Kaja", parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self._drag_pos: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        self.lbl = QLabel(title)
        self.lbl.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.lbl)
        layout.addStretch(1)

        self.btn_min = QPushButton("-")
        self.btn_min.setFixedSize(36, 26)
        self.btn_min.clicked.connect(self.request_minimize.emit)
        layout.addWidget(self.btn_min)

        self.btn_max = QPushButton("[]")
        self.btn_max.setFixedSize(36, 26)
        self.btn_max.clicked.connect(self.request_toggle_max.emit)
        layout.addWidget(self.btn_max)

        self.btn_close = QPushButton("X")
        self.btn_close.setFixedSize(36, 26)
        self.btn_close.clicked.connect(self.request_close.emit)
        layout.addWidget(self.btn_close)

    def set_maximized(self, maximized: bool):
        self.btn_max.setText("▢" if not maximized else "❐")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()
            e.accept()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.request_toggle_max.emit()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is None:
            return
        if e.buttons() & Qt.LeftButton:
            w = self.window()
            delta = e.globalPosition().toPoint() - self._drag_pos
            w.move(w.pos() + delta)
            self._drag_pos = e.globalPosition().toPoint()
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        e.accept()


def style_progress_bar(bar: QProgressBar, *, indeterminate: bool = False) -> None:
    """
    Standardize progress bar visuals to the popup look used for GPT queries.
    """
    bar.setFixedHeight(18)
    bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if indeterminate:
        bar.setRange(0, 0)
        bar.setTextVisible(False)
    else:
        if bar.minimum() == 0 and bar.maximum() == 0:
            bar.setRange(0, 100)
        bar.setTextVisible(True)


def style_message_box(box: QMessageBox) -> None:
    box.setStyleSheet(DARK_STYLESHEET)


def style_file_dialog(dlg: QFileDialog) -> None:
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    dlg.setStyleSheet(DARK_STYLESHEET)
    dlg.resize(900, 600)


def dialog_open_file(parent: QWidget, title: str, start_dir: str, name_filter: str) -> tuple[str, str]:
    dlg = QFileDialog(parent, title, start_dir)
    style_file_dialog(dlg)
    dlg.setFileMode(QFileDialog.ExistingFile)
    if name_filter:
        dlg.setNameFilter(name_filter)
    if dlg.exec():
        files = dlg.selectedFiles()
        return (files[0] if files else "", dlg.selectedNameFilter())
    return ("", "")


def dialog_save_file(parent: QWidget, title: str, default_name: str, name_filter: str) -> tuple[str, str]:
    dlg = QFileDialog(parent, title, default_name)
    style_file_dialog(dlg)
    dlg.setAcceptMode(QFileDialog.AcceptSave)
    dlg.setFileMode(QFileDialog.AnyFile)
    if name_filter:
        dlg.setNameFilter(name_filter)
    if dlg.exec():
        files = dlg.selectedFiles()
        return (files[0] if files else "", dlg.selectedNameFilter())
    return ("", "")


def dialog_select_dir(parent: QWidget, title: str, start_dir: str) -> str:
    dlg = QFileDialog(parent, title, start_dir)
    style_file_dialog(dlg)
    dlg.setFileMode(QFileDialog.Directory)
    dlg.setOption(QFileDialog.ShowDirsOnly, True)
    if dlg.exec():
        files = dlg.selectedFiles()
        return files[0] if files else ""
    return ""


def dialog_input_text(parent: QWidget, title: str, label: str, default: str = "") -> tuple[str, bool]:
    dlg = QInputDialog(parent)
    dlg.setOption(QInputDialog.UsePlainTextEditForTextInput, False)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    if default:
        dlg.setTextValue(default)
    dlg.setOption(QInputDialog.NoButtons, False)
    dlg.setStyleSheet(DARK_STYLESHEET)
    dlg.resize(520, 220)
    ok = dlg.exec() == QDialog.Accepted
    return (dlg.textValue(), ok)


def open_in_file_manager(path: str) -> bool:
    """Open path in OS file manager across Windows, macOS and Linux."""
    if not path:
        return False
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return False

    if QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
        return True

    try:
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except Exception:
        return False

class StyledMessageDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        title: str,
        text: str,
        *,
        buttons: List[tuple[str, int]],
        default_code: int | None = None,
        details: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._result_code = QMessageBox.NoButton
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setModal(True)
        self.setStyleSheet(DARK_STYLESHEET)
        self.resize(520, 220)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        self.titlebar = TitleBar(title, self)
        self.titlebar.lbl.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.titlebar.request_close.connect(self._on_close)
        v.addWidget(self.titlebar)

        self.lbl = QLabel(text)
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)

        if details:
            self.details = QPlainTextEdit()
            self.details.setReadOnly(True)
            self.details.setPlainText(details)
            v.addWidget(self.details, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        for label, code in buttons:
            btn = QPushButton(label)
            if default_code is not None and code == default_code:
                btn.setObjectName("PrimaryButton")
            btn.clicked.connect(lambda _=False, c=code: self._choose(c))
            row.addWidget(btn)
        v.addLayout(row)

    def _choose(self, code: int) -> None:
        self._result_code = code
        self.accept()

    def _on_close(self) -> None:
        self._result_code = QMessageBox.RejectRole
        self.reject()

    def exec(self) -> int:  # type: ignore[override]
        super().exec()
        return self._result_code


def msg_info(parent: QWidget, title: str, text: str, *, details: str | None = None) -> int:
    dlg = StyledMessageDialog(parent, title, text, buttons=[("OK", QMessageBox.Ok)], default_code=QMessageBox.Ok, details=details)
    return dlg.exec()


def msg_warning(parent: QWidget, title: str, text: str, *, details: str | None = None) -> int:
    dlg = StyledMessageDialog(parent, title, text, buttons=[("OK", QMessageBox.Ok)], default_code=QMessageBox.Ok, details=details)
    return dlg.exec()


def msg_critical(parent: QWidget, title: str, text: str, *, details: str | None = None) -> int:
    dlg = StyledMessageDialog(parent, title, text, buttons=[("OK", QMessageBox.Ok)], default_code=QMessageBox.Ok, details=details)
    return dlg.exec()


def msg_question(parent: QWidget, title: str, text: str, *, details: str | None = None) -> int:
    dlg = StyledMessageDialog(
        parent,
        title,
        text,
        buttons=[("Ano", QMessageBox.Yes), ("Ne", QMessageBox.No)],
        default_code=QMessageBox.Yes,
        details=details,
    )
    return dlg.exec()


class BusyPopup:
    """
    Lightweight modal-ish progress helper; shows an indeterminate bar and closes automatically.
    Usage:
        with BusyPopup(parent, "Loading..."):
            do_work()
    """

    def __init__(self, parent: QWidget, text: str = "Pracuji..."):
        self.dialog = QDialog(parent)
        self.dialog.setWindowTitle(text)
        self.dialog.setModal(True)
        self.dialog.setFixedSize(420, 150)
        self.dialog.setStyleSheet(DARK_STYLESHEET)
        layout = QVBoxLayout(self.dialog)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        self.label = QLabel(text)
        self.bar = QProgressBar()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        style_progress_bar(self.bar, indeterminate=True)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)

    def update_text(self, text: str):
        self.label.setText(text)
        self.dialog.setWindowTitle(text)
        QApplication.processEvents()

    def start(self):
        """Explicit start without context manager."""
        return self.__enter__()

    def __enter__(self):
        self.dialog.show()
        QApplication.processEvents()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self):
        try:
            self.dialog.accept()
        except Exception:
            try:
                self.dialog.close()
            except Exception:
                pass
