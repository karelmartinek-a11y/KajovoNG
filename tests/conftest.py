from __future__ import annotations

import os
import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest


@pytest.fixture(autouse=True)
def isolated_api_key_store(monkeypatch):
    """Testy nesmějí načítat ani měnit skutečný klíč v registru uživatele."""
    monkeypatch.setattr("kajovo.core.secret_store._read_persisted_api_key", lambda: None)
    def blocked(*args, **kwargs):
        raise AssertionError("Test musí nahradit trvalé ukládání API klíče.")
    monkeypatch.setattr("kajovo.ui.mainwindow.MainWindow._set_env_api_key", blocked)


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    """Regresní test nesmí provést skutečný síťový HTTP požadavek."""
    def blocked(*args, **kwargs):
        raise AssertionError("Test se pokusil o živý HTTP požadavek.")
    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    for module_name in ("httpx", "httpx2"):
        if importlib.util.find_spec(module_name):
            module = importlib.import_module(module_name)
            monkeypatch.setattr(module.Client, "send", blocked)
