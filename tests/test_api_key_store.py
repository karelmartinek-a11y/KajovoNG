"""API key storage bez použití skutečného registru nebo credential store uživatele."""

import os
import sys
from types import SimpleNamespace

import pytest

from kajovo.core import secret_store

READ_LEGACY = secret_store._read_persisted_api_key
READ_KEYRING = secret_store._read_keyring_api_key_record


class FakeRegistry:
    HKEY_CURRENT_USER = 1
    REG_SZ = 1
    KEY_READ = 2
    KEY_WRITE = 4

    def __init__(self):
        self.record = None
        self.fail_read = False
        self.fail_delete = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def OpenKey(self, root, path):
        assert (root, path) == (self.HKEY_CURRENT_USER, "Environment")
        if self.fail_read:
            raise PermissionError("test")
        return self

    def CreateKeyEx(self, root, path, reserved, access):
        assert access == self.KEY_READ | self.KEY_WRITE
        return self.OpenKey(root, path)

    def QueryValueEx(self, key, name):
        assert name == "OPENAI_API_KEY"
        if self.record is None:
            raise FileNotFoundError(name)
        return self.record

    def DeleteValue(self, key, name):
        assert name == "OPENAI_API_KEY"
        if self.fail_delete:
            raise PermissionError("test")
        if self.record is None:
            raise FileNotFoundError(name)
        self.record = None


class PasswordDeleteError(Exception):
    pass


class FakeKeyring:
    errors = SimpleNamespace(PasswordDeleteError=PasswordDeleteError)

    def __init__(self):
        self.record = None
        self.fail_read = False
        self.fail_write = False
        self.fail_delete = False
        self.wrong_verification = False
        self.written = False

    def _assert_address(self, service, account):
        assert service == secret_store.SERVICE_NAME
        assert account == secret_store.API_KEY_ACCOUNT

    def get_password(self, service, account):
        self._assert_address(service, account)
        if self.fail_read:
            raise PermissionError("test")
        if self.wrong_verification and self.written:
            return "nesprávně uložený testovací údaj"
        return self.record

    def set_password(self, service, account, value):
        self._assert_address(service, account)
        if self.fail_write:
            raise PermissionError("test")
        self.record = value
        self.written = True

    def delete_password(self, service, account):
        self._assert_address(service, account)
        if self.fail_delete:
            raise PermissionError("test")
        if self.record is None:
            raise PasswordDeleteError(account)
        self.record = None
        self.written = True


@pytest.fixture
def stores(monkeypatch):
    registry = FakeRegistry()
    keyring = FakeKeyring()
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setattr(secret_store, "os", SimpleNamespace(name="nt", environ=os.environ))
    # Autouse fixture blokuje skutečné persistentní store; tento test vrací reálné funkce.
    monkeypatch.setattr(secret_store, "_read_persisted_api_key", READ_LEGACY)
    monkeypatch.setattr(secret_store, "_read_keyring_api_key_record", READ_KEYRING)
    monkeypatch.setenv("OPENAI_API_KEY", "starý-testovací-klíč")
    return registry, keyring


@pytest.mark.parametrize("inherited", ["", "starý-testovací-klíč"])
def test_saved_key_wins_over_missing_or_stale_environment(stores, monkeypatch, inherited):
    registry, keyring = stores
    assert secret_store.persist_api_key("nový-testovací-klíč")
    assert registry.record is None
    assert keyring.record == "nový-testovací-klíč"
    monkeypatch.setenv("OPENAI_API_KEY", inherited)
    assert secret_store.load_api_key() == "nový-testovací-klíč"
    assert os.environ["OPENAI_API_KEY"] == "nový-testovací-klíč"


def test_no_saved_entry_uses_environment(stores):
    assert secret_store.load_api_key() == "starý-testovací-klíč"


def test_explicit_delete_survives_stale_parent_environment(stores, monkeypatch):
    registry, keyring = stores
    secret_store.persist_api_key("")
    assert registry.record is None
    assert keyring.record == secret_store._API_KEY_EMPTY_SENTINEL
    monkeypatch.setenv("OPENAI_API_KEY", "stale-parent-key")
    assert secret_store.load_api_key() == ""
    assert os.environ["OPENAI_API_KEY"] == ""


def test_legacy_registry_key_is_migrated_after_verified_keyring_write(stores):
    registry, keyring = stores
    registry.record = ("legacy-test-key", registry.REG_SZ)
    assert secret_store.load_api_key() == "legacy-test-key"
    assert keyring.record == "legacy-test-key"
    assert registry.record is None


def test_legacy_explicit_empty_is_migrated_as_explicit_empty(stores, monkeypatch):
    registry, keyring = stores
    registry.record = ("", registry.REG_SZ)
    monkeypatch.setenv("OPENAI_API_KEY", "stale-parent-key")
    assert secret_store.load_api_key() == ""
    assert keyring.record == secret_store._API_KEY_EMPTY_SENTINEL
    assert registry.record is None
    assert os.environ["OPENAI_API_KEY"] == ""


def test_failed_keyring_write_preserves_old_credential(stores):
    registry, keyring = stores
    keyring.record = "původní-credential"
    keyring.fail_write = True
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.persist_api_key("nový-testovací-klíč")
    assert keyring.record == "původní-credential"
    assert registry.record is None


def test_failed_verification_restores_previous_credential(stores):
    registry, keyring = stores
    keyring.record = "původní-credential"
    keyring.wrong_verification = True
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.persist_api_key("nový-testovací-klíč")
    assert keyring.record == "původní-credential"
    assert registry.record is None


def test_registry_read_failure_is_not_silent_when_keyring_is_empty(stores):
    registry, _ = stores
    registry.fail_read = True
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.load_api_key()


def test_invalid_registry_type_is_rejected(stores):
    registry, _ = stores
    registry.record = (123, 4)
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.load_api_key()


def test_keyring_read_failure_keeps_legacy_compatibility(stores):
    registry, keyring = stores
    registry.record = ("legacy-test-key", registry.REG_SZ)
    keyring.fail_read = True
    assert secret_store.load_api_key() == "legacy-test-key"
    assert registry.record == ("legacy-test-key", registry.REG_SZ)


def test_failed_migration_keeps_legacy_record(stores):
    registry, keyring = stores
    registry.record = ("legacy-test-key", registry.REG_SZ)
    keyring.fail_write = True
    with pytest.warns(RuntimeWarning, match="migrace"):
        assert secret_store.load_api_key() == "legacy-test-key"
    assert registry.record == ("legacy-test-key", registry.REG_SZ)


def test_legacy_delete_failure_rolls_back_new_credential(stores):
    registry, keyring = stores
    registry.record = ("legacy-test-key", registry.REG_SZ)
    keyring.record = "původní-credential"
    registry.fail_delete = True
    with pytest.raises(secret_store.APIKeyStoreError, match="vrácena"):
        secret_store.persist_api_key("nový-testovací-klíč")
    assert keyring.record == "původní-credential"
    assert registry.record == ("legacy-test-key", registry.REG_SZ)
