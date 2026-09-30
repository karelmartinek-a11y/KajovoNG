"""Kanonické vazby Studio stránky dávek na lokální stav a core dokončení."""

import json
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


def test_batch_page_renders_compact_card_without_identifiers(studio, tmp_path):
    run = make_run(studio, tmp_path)
    page = studio.batches
    page.records = [{
        "id": "batch_work",
        "remote": {"id": "batch_work", "status": "completed", "request_counts": {"total": 2, "completed": 2, "failed": 0}},
        "state": read_state(run),
        "run_dir": str(run),
        "kind": "GENERATE",
        "photo": None,
        "started_at": 1726050000,
    }]
    page.render()
    card = page.card_widgets["batch_work"]
    text = " ".join(label.text() for label in card.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert "Ukázkový projekt" not in text
    assert "Dokon" in text
    assert "2 z 2 úloh" in text
    assert "Čeká na stažení výsledků" in text
    assert "batch_work" not in text
    assert page.refresh_button.text() == "Obnovit"


def test_opening_batch_page_requests_quiet_status_refresh(studio, monkeypatch):
    calls = []
    monkeypatch.setattr(studio.context, "api_key", "test-key")
    monkeypatch.setattr(studio.batches, "refresh", lambda **kwargs: calls.append(kwargs))

    studio.batches.page_activated()

    assert calls == [{"quiet": True}]


def test_batch_complete_delegates_to_core_and_refreshes_local_evidence(studio, tmp_path, monkeypatch):
    run = make_run(studio, tmp_path)
    page = studio.batches
    record = {
        "id": "batch_work",
        "remote": {"id": "batch_work", "status": "completed"},
        "state": read_state(run),
        "run_dir": str(run),
        "kind": "GENERATE",
        "photo": None,
        "started_at": 1726050000,
    }
    page.records = [record]
    page.render()
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
    monkeypatch.setattr(page, "refresh", lambda *args, **kwargs: None)
    page.download(record)
    assert calls == [(run, "batch_work", True)]
    assert read_state(run)["status"] == "files_complete_unverified"
