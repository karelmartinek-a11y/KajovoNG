from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox, QMessageBox, QTextEdit, QFormLayout
from PySide6.QtCore import Qt
from .theme import DARK_STYLESHEET

from ..core.config import save_settings, DEFAULT_SETTINGS_FILE

class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SETTINGS")
        self.setModal(True)
        self.resize(640, 520)
        self.setStyleSheet(DARK_STYLESHEET)
        self.s = settings

        v = QVBoxLayout(self)

        form = QFormLayout()
        self.chk_mask = QCheckBox("Mask secrets in logs")
        self.chk_mask.setChecked(bool(self.s.logging.mask_secrets))
        self.chk_encrypt = QCheckBox("Encrypt logs (basic)")
        self.chk_encrypt.setChecked(bool(self.s.logging.encrypt_logs))
        self.chk_allow_sensitive = QCheckBox("Allow upload of sensitive files (danger)")
        self.chk_allow_sensitive.setChecked(bool(self.s.security.allow_upload_sensitive))

        self.sp_batch_poll = QDoubleSpinBox()
        self.sp_batch_poll.setRange(0.5, 60.0)
        self.sp_batch_poll.setSingleStep(0.5)
        self.sp_batch_poll.setValue(float(getattr(self.s, "batch_poll_interval_s", 4.0)))

        self.sp_batch_timeout = QSpinBox()
        self.sp_batch_timeout.setRange(60, 24*60*60)
        self.sp_batch_timeout.setValue(int(getattr(self.s, "batch_timeout_s", 3600)))

        self.txt_deny_ext = QTextEdit()
        self.txt_deny_ext.setPlaceholderText("Jedna přípona na řádek, např. .exe")
        self.txt_deny_ext.setPlainText("\n".join(self.s.security.deny_extensions_in or []))

        self.txt_deny_glob = QTextEdit()
        self.txt_deny_glob.setPlaceholderText("Jedna glob maska na řádek, např. **/.git/**")
        self.txt_deny_glob.setPlainText("\n".join(self.s.security.deny_globs_in or []))

        self.ed_price_url = QLineEdit()
        self.ed_price_url.setText(getattr(self.s.pricing, "source_url", ""))

        self.chk_price_refresh = QCheckBox("Auto refresh pricing on start")
        self.chk_price_refresh.setChecked(bool(getattr(self.s.pricing, "auto_refresh_on_start", True)))

        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.1)
        self.sp_temp.setValue(float(getattr(self.s, "default_temperature", 0.2)))

        form.addRow(QLabel("Logging"), QLabel(""))
        form.addRow(self.chk_mask)
        form.addRow(self.chk_encrypt)
        form.addRow(QLabel("Security"), QLabel(""))
        form.addRow(self.chk_allow_sensitive)
        form.addRow(QLabel("Deny extensions (IN mirror)"), self.txt_deny_ext)
        form.addRow(QLabel("Deny globs (IN mirror)"), self.txt_deny_glob)
        form.addRow(QLabel("Batch poll interval (s)"), self.sp_batch_poll)
        form.addRow(QLabel("Batch timeout (s)"), self.sp_batch_timeout)
        form.addRow(QLabel("Default temperature"), self.sp_temp)
        form.addRow(QLabel("Pricing source URL"), self.ed_price_url)
        form.addRow(self.chk_price_refresh)

        v.addLayout(form)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("Uložit")
        self.btn_close = QPushButton("Zavřít")
        btns.addStretch(1)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_close)
        v.addLayout(btns)

        self.btn_close.clicked.connect(self.close)
        self.btn_save.clicked.connect(self.on_save)

    def on_save(self):
        self.s.logging.mask_secrets = bool(self.chk_mask.isChecked())
        self.s.logging.encrypt_logs = bool(self.chk_encrypt.isChecked())
        self.s.security.allow_upload_sensitive = bool(self.chk_allow_sensitive.isChecked())

        deny_ext = [ln.strip() for ln in self.txt_deny_ext.toPlainText().splitlines() if ln.strip()]
        deny_glob = [ln.strip() for ln in self.txt_deny_glob.toPlainText().splitlines() if ln.strip()]
        self.s.security.deny_extensions_in = deny_ext
        self.s.security.deny_globs_in = deny_glob

        self.s.batch_poll_interval_s = float(self.sp_batch_poll.value())
        self.s.batch_timeout_s = int(self.sp_batch_timeout.value())
        self.s.default_temperature = float(self.sp_temp.value())

        self.s.pricing.source_url = self.ed_price_url.text().strip()
        self.s.pricing.auto_refresh_on_start = bool(self.chk_price_refresh.isChecked())

        try:
            save_settings(self.s, DEFAULT_SETTINGS_FILE)
            msg_info(self, "SETTINGS", "Uloženo.")
        except Exception as e:
            msg_critical(self, "SETTINGS", f"Chyba při ukládání: {e}")
