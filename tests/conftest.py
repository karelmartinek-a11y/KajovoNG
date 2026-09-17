from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from kajovo.core import secret_store


@pytest.fixture(autouse=True)
def isolated_api_key_store(monkeypatch):
    """Testy nesmějí načítat ani měnit skutečný registry/credential store uživatele."""
    monkeypatch.setattr(secret_store, "_read_persisted_api_key", lambda: None)
    monkeypatch.setattr(
        secret_store,
        "_read_keyring_api_key_record",
        lambda: secret_store._MISSING,
    )

    def blocked(*args, **kwargs):
        raise AssertionError("Test musí nahradit trvalé ukládání API klíče.")

    # Lightweight architecture lane záměrně neinstaluje PySide6. UI moduly proto
    # neimportujeme jen kvůli monkeypatchi; kanonický Studio alias blokujeme
    # pouze v prostředí, kde je Qt skutečně dostupné.
    if importlib.util.find_spec("PySide6") is not None:
        monkeypatch.setattr("kajovo.studio.settings.persist_api_key", blocked)


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    """Regresní test nesmí provést skutečný síťový HTTP požadavek."""

    def blocked(*args, **kwargs):
        raise AssertionError("Test se pokusil o živý HTTP požadavek.")

    # Architektonická lane neinstaluje provozní HTTP klienty. Pokud klient v
    # prostředí existuje, je vždy zablokován; jinak jej fixture sama neimportuje.
    if importlib.util.find_spec("requests") is not None:
        monkeypatch.setattr("requests.sessions.Session.request", blocked)
    for module_name in ("httpx", "httpx2"):
        if importlib.util.find_spec(module_name) is not None:
            module = importlib.import_module(module_name)
            monkeypatch.setattr(module.Client, "send", blocked)
