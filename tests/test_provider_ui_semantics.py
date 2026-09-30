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
        "kajovo.studio.resources.get_open_file_names",
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
    client.list_batches.return_value = []
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
            "kind": "GENERATE",
            "photo": None,
            "started_at": 1726050000,
        }
    ]
    page.render()
    monkeypatch.setattr("kajovo.studio.batches.confirm", lambda *args: True)

    page.cancel(page.records[0])

    client.cancel_batch.assert_called_once_with("batch_one")


def test_photo_progress_has_no_cancel_action(qtbot, tmp_path):
    client = Mock()
    remote = {"id": "batch_photo", "status": "cancelled", "request_counts": {}}
    client.cancel_batch.return_value = remote
    context = _context(qtbot, tmp_path, client)
    page = PhotosPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    assert not hasattr(page, "cancel_job")
    assert not any(button.text() == "Zrušit" for button in page.findChildren(type(page.start_button)))
    client.cancel_batch.assert_not_called()


def test_resources_cancelled_upload_never_calls_provider(qtbot, tmp_path, monkeypatch):
    client = Mock()
    context = _context(qtbot, tmp_path, client)
    page = ResourcesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    monkeypatch.setattr(
        "kajovo.studio.resources.get_open_file_names",
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
            "kind": "GENERATE",
            "photo": None,
            "started_at": 1726050000,
        }
    ]
    page.render()
    confirm_call = Mock(return_value=True)
    monkeypatch.setattr("kajovo.studio.batches.confirm", confirm_call)

    page.cancel(page.records[0])

    confirm_call.assert_not_called()
    client.cancel_batch.assert_not_called()


def test_photo_progress_refresh_is_the_only_global_action(qtbot, tmp_path):
    client = Mock()
    context = _context(qtbot, tmp_path, client)
    page = PhotosPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)
    assert page.tabs.tabText(3) == "Průběh úprav"
    assert not hasattr(page, "cancel_job")
    client.cancel_batch.assert_not_called()


def test_resource_store_add_calls_provider_and_missing_store_blocks_it(
    qtbot, tmp_path
):
    from kajovo.studio.resources import fill_records

    client = Mock()
    client.list_vector_store_files.return_value = [{"id": "file_one"}]
    context = _context(qtbot, tmp_path, client)
    page = ResourcesPage(context)
    qtbot.addWidget(page)
    _synchronous_execute(page, client)

    page.add_files(["file_one"])
    client.add_file_to_vector_store.assert_not_called()

    fill_records(page.lists["stores"], [{"id": "store_one"}])
    page.lists["stores"].setCurrentRow(0)
    page.add_files(["file_one"])

    client.add_file_to_vector_store.assert_called_once_with("store_one", "file_one")
    client.list_vector_store_files.assert_called_once_with("store_one")
