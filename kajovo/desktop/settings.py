"""Nastavení rozdělené podle účelu; tajemství neopouštějí chráněné úložiště."""

import copy
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QTabWidget, QCheckBox, QLineEdit
from ..core.config import save_settings, DEFAULT_SETTINGS_FILE
from ..core.secret_store import persist_api_key, APIKeyStoreError
from ..core.notifications import send_smtp_notification
from .design import column, row, label, button, form, text, number, editor, scroll
from .dialogs import msg_info, msg_warning
from .jobs import Jobs


class SettingsPage(QWidget):
    key_changed = Signal(str)
    saved = Signal()

    def __init__(self, settings, api_key, parent=None):
        super().__init__(parent)
        self.s = settings
        self.jobs = Jobs(self)
        layout = column(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        access = QWidget()
        al = column(access, 18)
        al.addWidget(label("OpenAI API klíč", "Heading"))
        self.api_key = text(api_key, "sk-…")
        self.api_key.setEchoMode(QLineEdit.Password)
        al.addWidget(self.api_key)
        al.addWidget(
            row(
                button("Zobrazit / skrýt", self.toggle_key),
                button("Uložit klíč", self.save_key, "Primary"),
                button("Smazat klíč", self.delete_key, "Danger"),
            )
        )
        al.addWidget(
            label(
                "Uložený klíč se aktivuje až po úspěšném ověření trvalého uložení. Hesla se nezapisují do souborů projektu.",
                "Hint",
            )
        )
        al.addStretch()
        tabs.addTab(access, "Přístup")
        general = QWidget()
        gl = column(general, 18)
        fields = form(gl)
        self.default_model = text(settings.default_model)
        self.default_temperature = number(settings.default_temperature, maximum=2, decimal=True)
        self.batch_poll = number(settings.batch_poll_interval_s, 0.5, 60, True)
        self.batch_timeout = number(settings.batch_timeout_s, 60, 86400, True)
        self.response_timeout = number(settings.response_timeout_s, 1, 86400, True)
        self.price_url = text(settings.pricing.source_url)
        self.price_refresh = QCheckBox("Obnovovat ceník při spuštění")
        self.price_refresh.setChecked(settings.pricing.auto_refresh_on_start)
        for name, field in (
            ("Výchozí model", self.default_model),
            ("Výchozí teplota", self.default_temperature),
            ("Kontrola dávky každých (s)", self.batch_poll),
            ("Limit sledování dávky (s)", self.batch_timeout),
            ("Čekání na API (s)", self.response_timeout),
            ("URL ceníku", self.price_url),
            ("Obnova cen", self.price_refresh),
        ):
            fields.addRow(name, field)
        gl.addStretch()
        tabs.addTab(scroll(general), "Provoz a ceny")
        security = QWidget()
        sl = column(security, 18)
        self.allow_sensitive = QCheckBox("Povolit nahrávání citlivých souborů")
        self.allow_sensitive.setChecked(settings.security.allow_upload_sensitive)
        sl.addWidget(self.allow_sensitive)
        sl.addWidget(
            label(
                "Redakce známých tajných polí je vždy aktivní. Šifrování logů není dostupné; chraňte adresář LOG.",
                "Hint",
            )
        )
        self.deny_ext = editor("\n".join(settings.security.deny_extensions_in or []))
        self.deny_glob = editor("\n".join(settings.security.deny_globs_in or []))
        sl.addWidget(label("Vyloučené přípony vstupu, jedna na řádek"))
        sl.addWidget(self.deny_ext)
        sl.addWidget(label("Vyloučené vzory cest, jeden na řádek"))
        sl.addWidget(self.deny_glob)
        tabs.addTab(scroll(security), "Bezpečnost")
        smtp = QWidget()
        ml = column(smtp, 18)
        mf = form(ml)
        self.smtp = {}
        for name, title in (
            ("host", "Server"),
            ("port", "Port"),
            ("username", "Uživatel"),
            ("password", "Heslo"),
            ("from_email", "Odesílatel"),
            ("to_email", "Příjemce"),
        ):
            value = getattr(settings.smtp, name)
            field = number(value, 1, 65535) if name == "port" else text(value)
            if name == "password":
                field.setEchoMode(QLineEdit.Password)
            self.smtp[name] = field
            mf.addRow(title, field)
        for name, title in (("use_tls", "STARTTLS"), ("use_ssl", "SSL")):
            field = QCheckBox(title)
            field.setChecked(getattr(settings.smtp, name))
            self.smtp[name] = field
            mf.addRow(field)
        self.smtp["use_tls"].toggled.connect(
            lambda checked: self.smtp["use_ssl"].setChecked(False) if checked else None
        )
        self.smtp["use_ssl"].toggled.connect(
            lambda checked: self.smtp["use_tls"].setChecked(False) if checked else None
        )
        ml.addWidget(button("Odeslat testovací zprávu", self.test_smtp))
        ml.addStretch()
        tabs.addTab(scroll(smtp), "Upozornění e-mailem")
        layout.addWidget(button("Uložit nastavení", self.save, "Primary"))

    def toggle_key(self):
        self.api_key.setEchoMode(
            QLineEdit.Normal
            if self.api_key.echoMode() == QLineEdit.Password
            else QLineEdit.Password
        )

    def _store_key(self, key):
        try:
            if not persist_api_key(key):
                raise APIKeyStoreError(
                    "Trvalé uložení se nepodařilo ověřit; aktivní klíč se nezměnil."
                )
        except Exception as exc:
            msg_warning(self, "API klíč", str(exc))
            return
        self.api_key.setText(key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.key_changed.emit(key)
        msg_info(self, "API klíč", "Klíč uložen a ověřen." if key else "Klíč trvale smazán.")

    def save_key(self):
        key = self.api_key.text().strip()
        if key:
            self._store_key(key)
        else:
            msg_warning(self, "API klíč", "Vyplňte klíč; pro smazání použijte Smazat klíč.")

    def delete_key(self):
        self._store_key("")

    def smtp_value(self):
        value = copy.deepcopy(self.s.smtp)
        for name, field in self.smtp.items():
            setattr(
                value,
                name,
                field.isChecked()
                if isinstance(field, QCheckBox)
                else field.value()
                if name == "port"
                else field.text(),
            )
        return value

    def save(self):
        candidate = copy.deepcopy(self.s)
        candidate.default_model = self.default_model.text().strip()
        candidate.default_temperature = self.default_temperature.value()
        candidate.batch_poll_interval_s = self.batch_poll.value()
        candidate.batch_timeout_s = self.batch_timeout.value()
        candidate.response_timeout_s = self.response_timeout.value()
        candidate.pricing.source_url = self.price_url.text().strip()
        candidate.pricing.auto_refresh_on_start = self.price_refresh.isChecked()
        candidate.security.allow_upload_sensitive = self.allow_sensitive.isChecked()
        candidate.security.deny_extensions_in = [
            line.strip() for line in self.deny_ext.toPlainText().splitlines() if line.strip()
        ]
        candidate.security.deny_globs_in = [
            line.strip() for line in self.deny_glob.toPlainText().splitlines() if line.strip()
        ]
        candidate.smtp = self.smtp_value()
        try:
            save_settings(candidate, DEFAULT_SETTINGS_FILE)
        except Exception as exc:
            msg_warning(self, "Nastavení není uloženo", str(exc))
            return
        self.s.__dict__.update(candidate.__dict__)
        self.saved.emit()
        msg_info(self, "Nastavení", "Nastavení uloženo.")

    def test_smtp(self):
        smtp = self.smtp_value()
        if not smtp.host or not smtp.from_email or not smtp.to_email:
            msg_warning(self, "SMTP", "Vyplňte server, odesílatele a příjemce.")
            return

        def received(result):
            ok, message = result
            (msg_info if ok else msg_warning)(
                self, "SMTP", "Testovací zpráva odeslána." if ok else message
            )

        self.jobs.start(
            "Test SMTP",
            lambda job: send_smtp_notification(
                smtp, "Kájovo NG – test", "Testovací oznámení z aplikace."
            ),
            received,
        )

    def get_state(self):
        values = {name: getattr(self, name).text() for name in ("default_model", "price_url")}
        values.update(
            {
                name: getattr(self, name).value()
                for name in (
                    "default_temperature",
                    "batch_poll",
                    "batch_timeout",
                    "response_timeout",
                )
            }
        )
        values.update(
            {name: getattr(self, name).isChecked() for name in ("price_refresh", "allow_sensitive")}
        )
        values.update(
            {name: getattr(self, name).toPlainText() for name in ("deny_ext", "deny_glob")}
        )
        from dataclasses import asdict

        values["smtp"] = asdict(self.smtp_value())
        values["smtp"]["password"] = ""
        return values

    def apply_state(self, values):
        if not isinstance(values, dict):
            raise ValueError("Nastavení v zadání musí být objekt.")
        aliases = {"temperature": "default_temperature", "auto_price": "price_refresh"}
        for key, value in values.items():
            name = aliases.get(key, key)
            if name in ("default_model", "price_url") and isinstance(value, str):
                getattr(self, name).setText(value)
            elif name in (
                "default_temperature",
                "batch_poll",
                "batch_timeout",
                "response_timeout",
            ) and type(value) in (int, float):
                getattr(self, name).setValue(value)
            elif name in ("price_refresh", "allow_sensitive") and type(value) is bool:
                getattr(self, name).setChecked(value)
            elif name in ("deny_ext", "deny_glob") and isinstance(value, str):
                getattr(self, name).setPlainText(value)
        smtp_aliases = {
            "user": "username",
            "from": "from_email",
            "to": "to_email",
            "tls": "use_tls",
            "ssl": "use_ssl",
        }
        for key, value in values.get("smtp", {}).items():
            name = smtp_aliases.get(key, key)
            if name not in self.smtp or name == "password":
                continue
            field = self.smtp[name]
            if isinstance(field, QCheckBox):
                field.setChecked(bool(value))
            elif name == "port":
                field.setValue(int(value))
            else:
                field.setText(str(value))
