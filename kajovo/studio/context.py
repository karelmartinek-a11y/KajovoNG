"""Sdílený stav účtu a zdrojů; žádná obrazovka nevlastní přístup ostatních."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from kajovo.core.openai_client import OpenAIClient


class StudioContext(QObject):
    models_changed = Signal()
    attachments_changed = Signal()
    key_changed = Signal()
    settings_changed = Signal()

    def __init__(self, settings, operations, *, api_key="", client_factory=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.operations = operations
        self.api_key = api_key
        self.client_factory = client_factory or OpenAIClient
        self.models = []
        self.files = []
        self.stores = []

    def client(self):
        if not self.api_key:
            raise ValueError("Nejprve uložte přístupový klíč v Nastavení.")
        return self.client_factory(self.api_key, timeout_s=self.settings.response_timeout_s)

    def set_key(self, key):
        self.api_key = key
        self.models = []
        self.files = []
        self.stores = []
        self.key_changed.emit()
        self.models_changed.emit()
        self.attachments_changed.emit()

    def refresh_models(self):
        client = self.client()
        key = self.api_key

        def receive(records):
            if self.api_key == key:
                self.models = sorted({row["id"] for row in records if row.get("id")})
                self.models_changed.emit()

        return self.operations.start("Načtení katalogu modelů", lambda task: client.list_models(), receive)
