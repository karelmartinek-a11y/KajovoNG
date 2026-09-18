"""Sdílený stav účtu a zdrojů; žádná obrazovka nevlastní přístup ostatních."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from kajovo.core.model_catalog import ModelCatalogCache
from kajovo.core.model_registry import models_for_usage, recommended_model
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
        self.model_records = {}
        self.models_source = "none"
        self.models_fetched_at = 0.0
        self._model_refresh_record = None
        self._model_cache = ModelCatalogCache(Path(settings.cache_dir) / "model_catalog.json")
        self.files = []
        self.stores = []
        self._load_cached_models()

    def client(self):
        if not self.api_key:
            raise ValueError("Nejprve uložte přístupový klíč v Nastavení.")
        return self.client_factory(self.api_key, timeout_s=self.settings.response_timeout_s)

    def _apply_model_snapshot(self, snapshot, source):
        self.model_records = dict(snapshot.get("models") or {})
        self.models = sorted(self.model_records)
        self.models_fetched_at = float(snapshot.get("fetched_at") or 0)
        self.models_source = source if self.models else "none"

    def _load_cached_models(self):
        snapshot = self._model_cache.load(self.api_key)
        self._apply_model_snapshot(snapshot, "cache")

    def set_key(self, key):
        self.api_key = key
        self._model_refresh_record = None
        self._load_cached_models()
        self.files = []
        self.stores = []
        self.key_changed.emit()
        self.models_changed.emit()
        self.attachments_changed.emit()

    def refresh_models(self):
        if self._model_refresh_record is not None and not self._model_refresh_record.terminal:
            return self._model_refresh_record
        client = self.client()
        key = self.api_key

        def receive(records):
            if self.api_key == key:
                snapshot = self._model_cache.save(key, list(records))
                self._apply_model_snapshot(snapshot, "live")
                self.models_changed.emit()

        record = self.operations.start(
            "Načtení katalogu modelů",
            lambda task: client.list_models(),
            receive,
        )
        self._model_refresh_record = record

        def release():
            if self._model_refresh_record is record:
                self._model_refresh_record = None

        record.worker.finished.connect(release)
        return record

    def ensure_models(self):
        if self.models or not self.api_key:
            return None
        return self.refresh_models()

    def models_for_usage(self, usage):
        return models_for_usage(self.models, usage)

    def recommended_model(self, usage):
        return recommended_model(self.models, usage)

    def model_details(self, model):
        record = self.model_records.get(model)
        if record is None:
            return {
                "id": model,
                "catalog": {},
                "capabilities": None,
                "usages": [],
                "catalog_source": self.models_source,
                "catalog_fetched_at": self.models_fetched_at,
            }
        return {
            **record,
            "catalog_source": self.models_source,
            "catalog_fetched_at": self.models_fetched_at,
        }
