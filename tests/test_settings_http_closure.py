"""Nastavení, katalog a credentials přes UI; izolované úložiště místo OS."""

import json

import pytest
from PySide6.QtWidgets import QPushButton

from kajovo.core import secret_store
from kajovo.core.config import AppSettings
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from kajovo.studio.settings import SettingsPage
from test_resources_http_closure import ResourcesHttp, client_for
from test_runtime_end_to_end import child

READ_RECORD = secret_store._read_keyring_api_key_record


class CredentialBackend:
    def __init__(self):
        self.records = {}
        self.fault = ""
        self.calls = []

    def get_password(self, service, account):
        self.calls.append(("read", account))
        return self.records.get((service, account))

    def set_password(self, service, account, value):
        self.calls.append(("write", account))
        if self.fault == "write":
            self.fault = ""
            raise OSError("syntetické selhání úložiště")
        self.records[service, account] = value + "-mismatch" if self.fault == "mismatch" else value
        self.fault = ""

    def delete_password(self, service, account):
        self.records.pop((service, account), None)


def fixture(qtbot, monkeypatch, tmp_path):
    import keyring
    backend = CredentialBackend()
    monkeypatch.setattr(secret_store, "_keyring_module", lambda: backend)
    monkeypatch.setattr(secret_store, "_read_keyring_api_key_record", READ_RECORD)
    monkeypatch.setattr(secret_store, "_delete_persisted_api_key", lambda: None)
    monkeypatch.setattr("kajovo.studio.settings.persist_api_key", secret_store.persist_api_key)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    monkeypatch.setenv("KAJOVO_SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "old-cache"))
    manager = Operations(None)
    transport = ResourcesHttp(tmp_path)
    context = StudioContext(settings, manager, api_key="synthetic-old", client_factory=lambda *a, **k: client_for(transport))
    page = SettingsPage(context)
    manager.setParent(page)
    qtbot.addWidget(page)
    return page, context, backend, transport


@pytest.mark.parametrize("fault", ["", "write", "mismatch"])
def test_settings_credential_ui_verified_readback_rollback_and_delete(qtbot, monkeypatch, tmp_path, fault):
    page, context, backend, _ = fixture(qtbot, monkeypatch, tmp_path)
    backend.records[secret_store.SERVICE_NAME, secret_store.API_KEY_ACCOUNT] = "synthetic-old"
    backend.fault = fault
    page.key.setText("synthetic-new")
    page.findChild(QPushButton, "settings.key.save").click()
    expected = "synthetic-old" if fault else "synthetic-new"
    assert context.api_key == expected
    assert secret_store.load_api_key() == expected
    assert backend.records[secret_store.SERVICE_NAME, secret_store.API_KEY_ACCOUNT] == expected
    assert "synthetic-" not in page.notice.text()
    assert not (tmp_path / "settings.json").exists()
    monkeypatch.setattr("kajovo.studio.settings.confirm", lambda *a: True)
    page.findChild(QPushButton, "settings.key.delete").click()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-inherited")
    assert secret_store.load_api_key() == "" and context.api_key == ""
    assert page.key.text() == ""


def settings_restart(root):
    from kajovo.core.config import load_settings
    from kajovo.studio.context import StudioContext
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    parent = QWidget()
    settings = load_settings(str(root / "settings.json"), resolve_secrets=False)
    assert settings.response_timeout_s == 42
    assert settings.default_model == "gpt-4o-mini"
    manager = Operations(parent)
    context = StudioContext(settings, manager, api_key="synthetic-old")
    assert context._model_cache.path == root / "new-cache" / "model_catalog.json"
    assert context.models == ["gpt-4o-mini"]
    assert context.client().timeout_s == 42
    parent.close()
    app.processEvents()


def test_settings_saved_new_process_and_real_catalog_consumer(qtbot, monkeypatch, tmp_path):
    page, context, _, _ = fixture(qtbot, monkeypatch, tmp_path)
    page.editors["cache_dir"].setText(str(tmp_path / "new-cache"))
    page.editors["response_timeout_s"].setValue(42)
    page.editors["default_model"].setText("gpt-4o-mini")
    page.findChild(QPushButton, "settings.save").click()
    assert page.notice.text() == "Nastavení bylo uloženo."
    assert context._model_cache.path == tmp_path / "new-cache" / "model_catalog.json"
    context.refresh_models()
    qtbot.waitUntil(lambda: not context.operations.active)
    record = next(iter(context.operations.records.values()))
    qtbot.addWidget(record.dialog)
    record.dialog.close()
    assert record.terminal == "completed"
    assert (tmp_path / "new-cache" / "model_catalog.json").is_file()
    assert not (tmp_path / "old-cache" / "model_catalog.json").exists()
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "synthetic-" not in raw
    assert json.loads(raw)["smtp"]["password"] == ""
    child("from pathlib import Path; import sys; from test_settings_http_closure import settings_restart; settings_restart(Path(sys.argv[1]))", tmp_path)


def test_catalog_cache_path_change_rejects_pending_old_result(qtbot, monkeypatch, tmp_path):
    page, context, _, transport = fixture(qtbot, monkeypatch, tmp_path)
    transport.block = "/models"
    context.refresh_models()
    qtbot.waitUntil(transport.entered.is_set)
    page.editors["cache_dir"].setText(str(tmp_path / "new-cache"))
    page.findChild(QPushButton, "settings.save").click()
    transport.release.set()
    qtbot.waitUntil(lambda: not context.operations.active)
    for record in context.operations.records.values():
        qtbot.addWidget(record.dialog)
        record.dialog.close()
    assert context.models == []
    assert not list(tmp_path.glob("*-cache/model_catalog.json"))


@pytest.mark.parametrize('fault', ['validation', 'write'])
def test_settings_invalid_or_failed_write_keeps_config_and_consumer(qtbot, monkeypatch, tmp_path, fault):
    import os
    page, context, _, _ = fixture(qtbot, monkeypatch, tmp_path)
    page.findChild(QPushButton, 'settings.save').click()
    path = tmp_path / 'settings.json'
    before = path.read_bytes()
    timeout = context.client().timeout_s
    if fault == 'validation':
        page.editors['response_timeout_s'].setValue(0)
    else:
        page.editors['response_timeout_s'].setValue(42)
        original = os.replace
        def replace(source, destination):
            if str(destination) == str(path):
                raise OSError('syntetická chyba zápisu konfigurace')
            return original(source, destination)
        monkeypatch.setattr(os, 'replace', replace)
    page.findChild(QPushButton, 'settings.save').click()
    assert path.read_bytes() == before
    assert context.client().timeout_s == timeout
    assert page.notice.text() != 'Nastavení bylo uloženo.'
    assert 'synthetic-' not in page.notice.text()
