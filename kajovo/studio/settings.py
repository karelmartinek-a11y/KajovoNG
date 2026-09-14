"""Nastavení studia se samostatnou validací a ověřenou persistencí klíče."""

from __future__ import annotations

import copy
import math
from dataclasses import fields, is_dataclass

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QPlainTextEdit, QSpinBox, QTabWidget, QWidget

from kajovo.core.config import save_settings
from kajovo.core.secret_store import persist_api_key
from .components import Form, action, actions, caption, confirm, scroll, vertical


LABELS = {
    "comic_library_dir": "Adresář knihovny komiksů",
    "log_dir": "Adresář evidence", "cache_dir": "Adresář pracovní paměti",
    "batch_poll_interval_s": "Interval kontroly dávky v sekundách",
    "batch_timeout_s": "Místní limit sledování dávky v sekundách",
    "response_timeout_s": "Limit síťového požadavku v sekundách",
    "response_poll_timeout_s": "Limit sledování odpovědi v sekundách",
    "default_model": "Výchozí model", "default_temperature": "Výchozí teplota",
    "dry_run_modify": "Připravovat úpravy bez zápisu", "ui_reduced_motion": "Omezit animace",
    "max_attempts": "Nejvyšší počet pokusů", "base_delay_s": "Počáteční prodleva v sekundách",
    "max_delay_s": "Nejvyšší prodleva v sekundách", "jitter_s": "Rozptyl prodlevy v sekundách",
    "circuit_breaker_failures": "Počet chyb před pozastavením",
    "circuit_breaker_cooldown_s": "Délka pozastavení v sekundách",
    "allow_upload_sensitive": "Povolit odesílání citlivých vstupních souborů",
    "deny_extensions_in": "Zakázané vstupní přípony, každá na samostatném řádku",
    "allow_extensions_in": "Povolené vstupní přípony, prázdné znamená bez omezení",
    "deny_globs_in": "Zakázané vzory cest, každý na samostatném řádku",
    "allow_globs_in": "Povolené vzory cest, prázdné znamená bez omezení",
    "host": "Server", "port": "Port", "username": "Přihlašovací jméno",
    "password": "Heslo", "use_tls": "Zabezpečit spojení po připojení",
    "use_ssl": "Zabezpečit spojení od začátku", "from_email": "Adresa odesílatele",
    "to_email": "Adresa příjemce", "user": "Uživatel", "key": "Soubor přihlašovacího klíče",
    "pin_required": "Vyžadovat ověření otisku vzdáleného počítače",
}


class SettingsPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.editors = {}
        root = vertical(self, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        access = QWidget()
        body = vertical(access)
        body.addWidget(caption("Přístup ke službě", "section"))
        form = Form()
        self.key = form.text("settings.api_key", "Přístupový klíč", context.api_key, secret=True)
        body.addWidget(form)
        body.addWidget(actions(action("settings.key.show", "Zobrazit nebo skrýt klíč", self.toggle_key),
                               action("settings.key.save", "Uložit klíč", self.save_key, "primary"),
                               action("settings.key.delete", "Odstranit klíč", self.remove_key, "danger")))
        body.addWidget(caption("Klíč se aktivuje až po ověření trvalého uložení; do souboru zadání se nezapisuje.", "muted"))
        body.addStretch()
        self.tabs.addTab(scroll(access), "Přístup")
        groups = [("", "Provoz"), ("retry", "Opakování"), ("security", "Vstupní soubory"), ("smtp", "Elektronická pošta"), ("ssh", "Vzdálený počítač")]
        for group, title in groups:
            obj = getattr(context.settings, group) if group else context.settings
            form = Form()
            for field in fields(obj):
                value = getattr(obj, field.name)
                if is_dataclass(value):
                    continue
                identifier = f"{group}.{field.name}" if group else field.name
                label = LABELS[field.name]
                if isinstance(value, bool):
                    widget = form.check(identifier, label, value)
                elif isinstance(value, (float, int)):
                    widget = QSpinBox() if isinstance(value, int) else QDoubleSpinBox()
                    widget.setRange(0, 2_000_000_000)
                    if field.name == "default_temperature":
                        widget.setRange(0, 2)
                    if field.name == "port":
                        widget.setRange(1, 65535)
                    widget.setValue(value)
                    form.add(identifier, label, widget)
                elif isinstance(value, list) or value is None:
                    widget = QPlainTextEdit("\n".join(value or []))
                    widget.setMinimumHeight(100)
                    form.add(identifier, label, widget)
                else:
                    widget = form.text(identifier, label, value, secret=field.name == "password")
                self.editors[identifier] = widget
            container = QWidget()
            body = vertical(container)
            body.addWidget(form)
            if group == "smtp":
                body.addWidget(action("settings.smtp.test", "Odeslat zkušební zprávu", self.test_mail))
            body.addStretch()
            self.tabs.addTab(scroll(container), title)
        self.notice = caption("Změny se projeví po uložení.", "muted")
        root.addWidget(self.notice)
        root.addWidget(actions(action("settings.save", "Uložit nastavení", self.save, "primary")))
        context.settings_changed.connect(self.refresh_default)

    def refresh_default(self):
        self.editors["default_model"].setText(self.context.settings.default_model)

    def toggle_key(self):
        from PySide6.QtWidgets import QLineEdit
        self.key.setEchoMode(QLineEdit.Normal if self.key.echoMode() == QLineEdit.Password else QLineEdit.Password)

    def save_key(self):
        value = self.key.text().strip()
        if not value:
            self.notice.setText("Vyplňte přístupový klíč.")
            return
        try:
            if not persist_api_key(value):
                raise ValueError("Trvalé uložení klíče nebylo ověřeno.")
        except Exception as error:
            self.notice.setText(str(error))
            return
        self.context.set_key(value)
        self.notice.setText("Přístupový klíč byl uložen a aktivován.")

    def remove_key(self):
        if not confirm(self, "Odstranit přístupový klíč", "Opravdu chcete odstranit uložený přístupový klíč?"):
            return
        try:
            if not persist_api_key(""):
                raise ValueError("Odstranění klíče nebylo ověřeno.")
        except Exception as error:
            self.notice.setText(str(error))
            return
        self.key.clear()
        self.context.set_key("")
        self.notice.setText("Přístupový klíč byl odstraněn.")

    def snapshot(self):
        settings = copy.deepcopy(self.context.settings)
        for identifier, widget in self.editors.items():
            group, _, name = identifier.rpartition(".")
            target = getattr(settings, group) if group else settings
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                value = widget.value()
            elif isinstance(widget, QPlainTextEdit):
                value = [line.strip() for line in widget.toPlainText().splitlines() if line.strip()]
                if not value and name.startswith("allow_"):
                    value = None
            else:
                value = widget.text()
            setattr(target, name, value)
        if settings.smtp.use_tls and settings.smtp.use_ssl:
            raise ValueError("Vyberte pouze jeden způsob zabezpečení poštovního spojení.")
        positive = [settings.retry.max_attempts, settings.retry.circuit_breaker_failures,
                    settings.batch_poll_interval_s, settings.batch_timeout_s,
                    settings.response_timeout_s, settings.response_poll_timeout_s]
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("Časové limity a počty pokusů musí být větší než nula.")
        return settings

    def save(self):
        try:
            settings = self.snapshot()
            save_settings(settings)
        except Exception as error:
            self.notice.setText(str(error))
            return
        for field in fields(settings):
            setattr(self.context.settings, field.name, getattr(settings, field.name))
        self.context.operations.reduced_motion = settings.ui_reduced_motion
        for record in self.context.operations.records.values():
            record.dialog.reduced_motion = settings.ui_reduced_motion
            record.dialog.mark.set_running(not record.terminal, settings.ui_reduced_motion)
        self.notice.setText("Nastavení bylo uloženo.")
        self.context.settings_changed.emit()

    def test_mail(self):
        from kajovo.core.notifications import send_smtp_notification

        try:
            settings = self.snapshot()
            if not settings.smtp.host or not settings.smtp.to_email:
                raise ValueError("Vyplňte poštovní server a příjemce.")
        except ValueError as error:
            self.notice.setText(str(error))
            return

        def execute(task):
            success, message = send_smtp_notification(settings.smtp, "Kájovo NG · ověření oznámení", "Nastavení oznámení funguje.", raise_errors=True)
            if not success:
                raise RuntimeError(message)
            return message

        self.context.operations.start("Odeslání zkušební zprávy", execute,
                                      lambda value: self.notice.setText("Zkušební zpráva byla odeslána."))
