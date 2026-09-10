"""Uložení a načtení klíče přes náhradní registr, bez skutečných přihlašovacích údajů."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from kajovo.core import secret_store

READ_PERSISTED = secret_store._read_persisted_api_key


class FakeRegistry:
    HKEY_CURRENT_USER = 1
    REG_SZ = 1
    KEY_READ = 2
    KEY_WRITE = 4

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.record = None
        if self.path and self.path.exists():
            self.record = tuple(json.loads(self.path.read_text(encoding="utf-8")))
        self.fail_read = False
        self.fail_write = False
        self.wrong_verification = False
        self.written = False

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
        if self.wrong_verification and self.written:
            return "nesprávně uložený testovací údaj", self.REG_SZ
        return self.record

    def SetValueEx(self, key, name, reserved, kind, value):
        if self.fail_write:
            raise PermissionError("test")
        self.record = value, kind
        self.written = True
        if self.path:
            self.path.write_text(json.dumps(self.record), encoding="utf-8")

    def DeleteValue(self, key, name):
        self.record = None


@pytest.fixture
def registry(monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(secret_store, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(secret_store, "_read_persisted_api_key", READ_PERSISTED)
    monkeypatch.setenv("OPENAI_API_KEY", "starý-testovací-klíč")
    return fake


@pytest.mark.parametrize("inherited", ["", "starý-testovací-klíč"])
def test_saved_key_wins_over_missing_or_stale_environment(registry, monkeypatch, inherited):
    assert secret_store.persist_api_key("nový-testovací-klíč")
    monkeypatch.setenv("OPENAI_API_KEY", inherited)
    assert secret_store.load_api_key() == "nový-testovací-klíč"
    assert os.environ["OPENAI_API_KEY"] == "nový-testovací-klíč"


def test_no_saved_entry_uses_environment(registry):
    assert secret_store.load_api_key() == "starý-testovací-klíč"


def test_delete_survives_stale_parent_environment(registry):
    secret_store.persist_api_key("")
    assert registry.record == ("", registry.REG_SZ)
    assert secret_store.load_api_key() == ""
    assert os.environ["OPENAI_API_KEY"] == ""


def test_failed_write_preserves_old_key(registry):
    registry.record = "původní-uložený-klíč", registry.REG_SZ
    registry.fail_write = True
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.persist_api_key("nový-testovací-klíč")
    assert registry.record[0] == "původní-uložený-klíč"
    assert os.environ["OPENAI_API_KEY"] == "starý-testovací-klíč"


@pytest.mark.parametrize("previous", [None, ("původní-uložený-klíč", 1)])
def test_failed_verification_restores_registry(registry, previous):
    registry.record = previous
    registry.wrong_verification = True
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.persist_api_key("nový-testovací-klíč")
    assert registry.record == previous
    assert os.environ["OPENAI_API_KEY"] == "starý-testovací-klíč"


def test_registry_read_failure_is_not_silent_fallback(registry):
    registry.fail_read = True
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.load_api_key()


def test_invalid_registry_type_is_rejected(registry):
    registry.record = 123, 4
    with pytest.raises(secret_store.APIKeyStoreError):
        secret_store.load_api_key()


def test_save_then_load_in_separate_processes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fake_path = tmp_path / "registry.json"
    program = """
import os, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from test_api_key_store import FakeRegistry
from kajovo.core import secret_store
sys.modules['winreg'] = FakeRegistry(sys.argv[2])
secret_store.os = SimpleNamespace(name='nt', environ=os.environ)
if sys.argv[3] == 'save':
    assert secret_store.persist_api_key('dummy-persisted-key')
else:
    assert secret_store.load_api_key() == 'dummy-persisted-key'
    assert os.environ['OPENAI_API_KEY'] == 'dummy-persisted-key'
"""
    for action in ("save", "load"):
        env = {**os.environ, "OPENAI_API_KEY": "dummy-stale-parent-key"}
        subprocess.run([sys.executable, "-c", program, str(root / "tests"), str(fake_path), action],
                       cwd=root, env=env, check=True, capture_output=True)
