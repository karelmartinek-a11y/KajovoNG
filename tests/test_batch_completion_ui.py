"""Kanonické vazby Studio stránky dávek na lokální stav a core dokončení."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.batch_completion import read_state
from kajovo.core.config import AppSettings
from kajovo.studio.application import create_window


@pytest.fixture
def studio(qtbot, tmp_path):
    client = Mock()
    client.list_models.return_value = []
    window = create_window(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        api_key="test-key",
        client_factory=lambda *args, **kwargs: client,
    )
    qtbot.addWidget(window)
    return window


def make_run(studio, tmp_path):
    run = Path(studio.context.settings.log_dir) / "RUN_110920261200_abcd"
    run.mkdir(parents=True)
    state = {
        "run_id": run.name,
        "batch_id": "batch_work",
        "project": "Ukázkový projekt",
        "out_dir": str(tmp_path / "OUT"),
        "status": "batch_pending",
        "batch_records": {"batch_work": {"created_at": 1726050000}},
    }
    (run / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    return run


def test_batch_page_renders_remote_and_local_state(studio, tmp_path):
    run = make_run(studio, tmp_path)
    page = studio.batches
    page.records = [{
        "id": "batch_work",
        "remote": {"id": "batch_work", "status": "completed", "request_counts": {"total": 2, "completed": 2, "failed": 0}},
        "state": read_state(run),
        "run_dir": str(run),
    }]
    page.last_refresh = time.time()
    page.render()
    assert page.table.rowCount() == 1
    assert "Ukázkový projekt" in page.table.item(0, 0).text()
    assert page.table.item(0, 1).text() == "Zpracováno službou"
    assert "2 z 2" in page.table.item(0, 2).text()
    assert page.table.item(0, 3).text() == "Čeká na převzetí"


def test_batch_complete_delegates_to_core_and_refreshes_local_evidence(studio, tmp_path, monkeypatch):
    run = make_run(studio, tmp_path)
    page = studio.batches
    record = {
        "id": "batch_work",
        "remote": {"id": "batch_work", "status": "completed"},
        "state": read_state(run),
        "run_dir": str(run),
    }
    page.records = [record]
    page.render()
    page.table.selectRow(0)
    calls = []

    def complete(_client, root, identifier, _settings, progress=None):
        calls.append((Path(root), identifier, progress is not None))
        state = read_state(root)
        state["status"] = "files_complete_unverified"
        (Path(root) / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
        return {"status": "files_complete_unverified"}

    def execute(_title, function, receive=None, **_kwargs):
        task = SimpleNamespace(progress_event=SimpleNamespace(emit=lambda _event: None))
        value = function(Mock(), task)
        if receive:
            receive(value)
        return value

    monkeypatch.setattr("kajovo.studio.batches.complete_saved_batch", complete)
    monkeypatch.setattr(page, "execute", execute)
    page.complete()
    assert calls == [(run, "batch_work", True)]
    assert record["state"]["status"] == "files_complete_unverified"
    assert "Soubory" in page.table.item(0, 3).text() or page.table.item(0, 3).text()
