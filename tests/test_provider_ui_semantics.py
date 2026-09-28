"""Přímé Studio -> provider akce jsou vykonány pouze proti lokálním mockům."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QDialog, QWidget

from kajovo.core.config import AppSettings
from kajovo.studio.batches import BatchesPage
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from kajovo.studio.photos import PhotosPage
from kajovo.studio.resources import ResourcesPage


def _context(qtbot, tmp_path, client):
    parent = QWidget()
    qtbot.addWidget(parent)
    operations = Operations(parent)
    context = StudioContext(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        operations,
        api_key="test",
        client_factory=lambda *args, **kwargs: client,
    )
    return context


def _synchronous_execute(page, client):
    def execute(_title, operation, receive=None, **_kwargs):
        task = SimpleNamespace(
            progress_event=SimpleNamespace(emit=Mock()),
            logline=SimpleNamespace(emit=Mock()),
        )
        value = operation(client, task)
        if receive:
            receive(value)
        return value

    page.execute = execute


def test_resources_upload_executes_mock_provider_and_refreshes(qtbot, tmp_path, monkeypatch):
    client = Mock()
    client.upload_file.return_value = {"id": "file_uploaded"}
    client.list_files.return_value = [{"id": "file_uploaded", "filename": "a.txt"}]
    context = _context(qtbot, tmp_path, client)
    page = ResourcesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)

    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    monkeypatch.setattr(
        "kajovo.studio.resources.QFileDialog.getOpenFileNames",
        lambda *args, **kwargs: ([str(first), str(second)], ""),
    )

    page.upload()

    assert [call.args[0] for call in client.upload_file.call_args_list] == [
        str(first),
        str(second),
    ]
    client.list_files.assert_called_once_with()
    assert page.lists["files"].count() == 1


def test_resources_create_store_executes_mock_provider(qtbot, tmp_path, monkeypatch):
    client = Mock()
    client.create_vector_store.return_value = {"id": "vs_new"}
    client.list_vector_stores.return_value = [{"id": "vs_new", "name": "Nové"}]
    context = _context(qtbot, tmp_path, client)
    page = ResourcesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)

    class AcceptedValue:
        value = "Nové"

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr("kajovo.studio.resources.ValueDialog", AcceptedValue)

    page.create_store()

    client.create_vector_store.assert_called_once_with("Nové")
    client.list_vector_stores.assert_called_once_with()
    assert page.lists["stores"].count() == 1


def test_batch_cancel_calls_provider_once_for_cancellable_selection(
    qtbot, tmp_path, monkeypatch
):
    client = Mock()
    client.cancel_batch.return_value = {"id": "batch_one", "status": "cancelling"}
    context = _context(qtbot, tmp_path, client)
    page = BatchesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    page.records = [
        {
            "id": "batch_one",
            "remote": {"id": "batch_one", "status": "in_progress"},
            "state": {},
            "run_dir": "",
        }
    ]
    page.render()
    page.table.selectRow(0)
    monkeypatch.setattr("kajovo.studio.batches.confirm", lambda *args: True)

    page.cancel()

    client.cancel_batch.assert_called_once_with("batch_one")
    assert "zrušení" in page.notice.text().lower()


def test_photo_cancel_calls_provider_and_persists_returned_status(
    qtbot, tmp_path, monkeypatch
):
    client = Mock()
    remote = {"id": "batch_photo", "status": "cancelled", "request_counts": {}}
    client.cancel_batch.return_value = remote
    context = _context(qtbot, tmp_path, client)
    page = PhotosPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    job = SimpleNamespace(batch_id="batch_photo", status="in_progress")
    monkeypatch.setattr(page, "selected_job", lambda: job)
    monkeypatch.setattr("kajovo.studio.photos.confirm", lambda *args: True)
    apply_status = Mock()
    save_job = Mock()
    monkeypatch.setattr("kajovo.studio.photos.photo_batch.apply_batch_status", apply_status)
    monkeypatch.setattr("kajovo.studio.photos.photo_batch.save_job", save_job)
    monkeypatch.setattr(page, "load_jobs", Mock())

    page.cancel_job()

    client.cancel_batch.assert_called_once_with("batch_photo")
    apply_status.assert_called_once_with(job, remote)
    save_job.assert_called_once_with(job, context.settings.log_dir)


def test_resources_cancelled_upload_never_calls_provider(qtbot, tmp_path, monkeypatch):
    client = Mock()
    context = _context(qtbot, tmp_path, client)
    page = ResourcesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    monkeypatch.setattr(
        "kajovo.studio.resources.QFileDialog.getOpenFileNames",
        lambda *args, **kwargs: ([], ""),
    )

    page.upload()

    client.upload_file.assert_not_called()
    client.list_files.assert_not_called()


def test_resources_cancelled_store_dialog_never_calls_provider(qtbot, tmp_path, monkeypatch):
    client = Mock()
    context = _context(qtbot, tmp_path, client)
    page = ResourcesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)

    class RejectedValue:
        value = None

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("kajovo.studio.resources.ValueDialog", RejectedValue)

    page.create_store()

    client.create_vector_store.assert_not_called()
    client.list_vector_stores.assert_not_called()


def test_batch_cancel_ignores_non_cancellable_selection(qtbot, tmp_path, monkeypatch):
    client = Mock()
    context = _context(qtbot, tmp_path, client)
    page = BatchesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    page.records = [
        {
            "id": "batch_done",
            "remote": {"id": "batch_done", "status": "completed"},
            "state": {},
            "run_dir": "",
        }
    ]
    page.render()
    page.table.selectRow(0)
    confirm_call = Mock(return_value=True)
    monkeypatch.setattr("kajovo.studio.batches.confirm", confirm_call)

    page.cancel()

    confirm_call.assert_not_called()
    client.cancel_batch.assert_not_called()


def test_photo_cancel_without_batch_id_never_calls_provider(qtbot, tmp_path, monkeypatch):
    client = Mock()
    context = _context(qtbot, tmp_path, client)
    page = PhotosPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    monkeypatch.setattr(
        page,
        "selected_job",
        lambda: SimpleNamespace(batch_id="", status="preparing"),
    )
    confirm_call = Mock(return_value=True)
    monkeypatch.setattr("kajovo.studio.photos.confirm", confirm_call)

    page.cancel_job()

    confirm_call.assert_not_called()
    client.cancel_batch.assert_not_called()
