from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question

import os, subprocess
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
from PySide6.QtCore import Qt
from .theme import DARK_STYLESHEET

class ApiKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API-KEY")
        self.setModal(True)
        self.resize(520, 180)
        self.setStyleSheet(DARK_STYLESHEET)

        v = QVBoxLayout(self)

        v.addWidget(QLabel("OPENAI_API_KEY (uložení do user env proměnné + aktuální běh)"))

        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText("sk-...")
        v.addWidget(self.edit)

        row = QHBoxLayout()
        self.btn_show = QPushButton("Zobraz")
        self.btn_save = QPushButton("Uložit")
        self.btn_del = QPushButton("Smazat")
        self.btn_close = QPushButton("Zavřít")
        row.addWidget(self.btn_show)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        self.btn_close.clicked.connect(self.close)
        self.btn_show.clicked.connect(self.on_show)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_del.clicked.connect(self.on_delete)

    def on_show(self):
        val = os.environ.get("OPENAI_API_KEY", "")
        if not val:
            msg_info(self, "API-KEY", "OPENAI_API_KEY není nastavena.")
            return
        self.edit.setText(val)
        self.edit.setEchoMode(QLineEdit.Normal)

    def _setx(self, value: str) -> bool:
        # Works on Windows; on other OS fallback only for current process
        try:
            if os.name == "nt":
                subprocess.run(["setx", "OPENAI_API_KEY", value], capture_output=True, text=True, check=True, shell=False)
                return True
        except Exception:
            return False
        return False

    def on_save(self):
        val = (self.edit.text() or "").strip()
        if not val:
            msg_warning(self, "API-KEY", "Prázdný klíč nelze uložit.")
            return
        os.environ["OPENAI_API_KEY"] = val
        ok = self._setx(val)
        msg_info(self, "API-KEY", "Uloženo." + ("" if ok else " (Jen pro aktuální běh.)"))
        self.edit.setEchoMode(QLineEdit.Password)

    def on_delete(self):
        os.environ["OPENAI_API_KEY"] = ""
        ok = self._setx("")
        msg_info(self, "API-KEY", "Smazáno (nastaveno na prázdnou hodnotu)." + ("" if ok else " (Jen pro aktuální běh.)"))
        self.edit.clear()
        self.edit.setEchoMode(QLineEdit.Password)
