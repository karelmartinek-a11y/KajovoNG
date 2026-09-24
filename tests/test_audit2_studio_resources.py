"""Dílčí smazání vzdálených příloh se promítá i při chybě operace."""

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QWidget

from kajovo.core.config import AppSettings
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from kajovo.studio.resources import ResourcesPage, fill_records


@pytest.mark.parametrize("kind", ["files", "stores"])
@pytest.mark.parametrize("failure", ["delete", "list"])
def test_partial_delete_prunes_successes_even_when_worker_fails(qtbot, tmp_path, monkeypatch, kind, failure):
    parent = QWidget()
    qtbot.addWidget(parent)
    manager = Operations(parent)
    client = Mock()
    delete = client.delete_file if kind == "files" else client.delete_vector_store
    listing = client.list_files if kind == "files" else client.list_vector_stores
    if failure == "delete":
        delete.side_effect = [None, OSError("Smazání selhalo")]
    else:
        listing.side_effect = OSError("Výpis selhal")
    settings = AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG"))
    context = StudioContext(settings, manager, api_key="test", client_factory=lambda *args, **kwargs: client)
    page = ResourcesPage(context)
    qtbot.addWidget(page)
    setattr(context, kind, ["a", "b", "c"])
    fill_records(page.lists[kind], [{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr("kajovo.studio.resources.confirm", lambda *args: True)
    page.delete(kind, all_items=True)
    record = next(iter(manager.records.values()))
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == "failed"
    expected = ["b", "c"] if failure == "delete" else ["c"]
    assert getattr(context, kind) == expected
    assert page.lists[kind].count() == (1 if failure == "delete" else 0)
    record.dialog.close()

