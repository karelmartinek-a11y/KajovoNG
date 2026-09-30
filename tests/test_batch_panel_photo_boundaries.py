"""Panel Dávky sdílí PHOTO transakci; vadný job nesmí skrýt zdravé výsledky."""
import base64
import json
import threading
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt

from kajovo.core import photo_batch
from kajovo.core.config import AppSettings
from kajovo.studio.batches import BatchesPage
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from test_photo_studio import _frozen_job, _image_bytes, _prepare_import_job


def setup(qtbot, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _, job = _frozen_job(source)
    log_dir = tmp_path / "LOG"
    _prepare_import_job(job, log_dir)
    job.batch_id = "batch_good"
    job.status = "in_progress"
    photo_batch.save_job(job, log_dir)
    payload = {"id": job.batch_id, "input_file_id": job.input_file_id,
               "endpoint": "/v1/images/edits", "status": "completed",
               "output_file_id": "file_ready", "error_file_id": None,
               "request_counts": {"total": 1, "completed": 1, "failed": 0}}
    client = Mock()
    client.list_batches.return_value = [payload]
    client.retrieve_batch.return_value = payload
    operations = Operations(None)
    context = StudioContext(AppSettings(log_dir=str(log_dir), cache_dir=str(tmp_path / "cache")),
                            operations, api_key="offline", client_factory=lambda *_a, **_k: client)
    page = BatchesPage(context)
    operations.setParent(page)
    qtbot.addWidget(page)
    return job, client, page, context, log_dir


def finish(qtbot, context):
    qtbot.waitUntil(lambda: not context.operations.active, timeout=10000)
    for record in context.operations.records.values():
        qtbot.addWidget(record.dialog)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("fault", ["invalid_json", "foreign_identity"])
def test_batch_panel_keeps_healthy_photo_and_reports_bad_evidence(qtbot, tmp_path, fault):
    good, client, page, context, log_dir = setup(qtbot, tmp_path)
    bad_source = tmp_path / "bad_source"
    bad_source.mkdir()
    _, bad = _frozen_job(bad_source)
    _prepare_import_job(bad, log_dir)
    bad.batch_id, bad.status = "batch_bad", "in_progress"
    path = photo_batch.save_job(bad, log_dir) / "photo_job.json"
    if fault == "invalid_json":
        path.write_bytes(b"{invalid")
    else:
        payload = dict(client.list_batches.return_value[0], id="batch_bad", input_file_id="file_foreign")
        client.list_batches.return_value.insert(0, payload)
    before = path.read_bytes()
    qtbot.mouseClick(page.refresh_button, Qt.LeftButton)
    finish(qtbot, context)
    healthy = next((r for r in page.records if r["photo"] and r["photo"].job_id == good.job_id), None)
    assert healthy is not None, "Vadný PHOTO job skryl zdravou položku v panelu Dávky."
    assert healthy["photo"].status == "completed"
    assert healthy["photo"].output_file_id == "file_ready"
    assert path.read_bytes() == before
    assert bad.job_id in page.notice.text()
    assert "nepodařilo" in page.notice.text()
    client.upload_file.assert_not_called()
    client.create_image_batch.assert_not_called()


def test_batch_panel_refresh_reloads_photo_after_concurrent_download(qtbot, tmp_path, monkeypatch):
    job, client, page, context, log_dir = setup(qtbot, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = photo_batch.load_jobs
    def delayed_load(*args, **kwargs):
        rows = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return rows
    monkeypatch.setattr(photo_batch, "load_jobs", delayed_load)
    qtbot.mouseClick(page.refresh_button, Qt.LeftButton)
    try:
        qtbot.waitUntil(entered.is_set, timeout=10000)
        image = _image_bytes()
        client.file_content.return_value = (json.dumps({
            "custom_id": job.items[0].custom_id,
            "response": {"status_code": 200, "body": {"data": [{"b64_json": base64.b64encode(image).decode()}]}},
            "error": None,
        }) + "\n").encode()
        imported = photo_batch.download_results(client, job, log_dir)
        assert imported.status == "downloaded"
    finally:
        release.set()
    finish(qtbot, context)
    restored = original(log_dir)[0]
    assert restored.status == "downloaded"
    assert restored.items[0].status == "downloaded"
    assert restored.items[0].output_path == imported.items[0].output_path
    from pathlib import Path
    assert Path(restored.items[0].output_path).read_bytes() == image
    assert page.records[0]["photo"].status == "downloaded"
    client.create_image_batch.assert_not_called()


@pytest.mark.parametrize("change", ["account", "log_dir"])
def test_batch_panel_late_worker_does_not_replace_new_context(qtbot, tmp_path, change):
    _job, client, page, context, _root = setup(qtbot, tmp_path)
    entered, release = threading.Event(), threading.Event()
    payload = client.list_batches.return_value
    def delayed_batches():
        entered.set()
        assert release.wait(5)
        return payload
    client.list_batches.side_effect = delayed_batches
    qtbot.mouseClick(page.refresh_button, Qt.LeftButton)
    try:
        qtbot.waitUntil(entered.is_set, timeout=10000)
        if change == "account":
            context.set_key("other-account")
        else:
            context.settings.log_dir = str(tmp_path / "new-log")
            context.settings_changed.emit()
            page.reset()
    finally:
        release.set()
    finish(qtbot, context)
    assert not page.records
    assert page.refresh_button.isEnabled()
    assert client.list_batches.call_count == 1
    client.create_image_batch.assert_not_called()


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("raw", [b"{invalid", b'{"generate_batches":[]}'])
def test_broken_run_evidence_cannot_hide_healthy_photo(qtbot, tmp_path, remote, raw):
    job, _client, page, context, root = setup(qtbot, tmp_path)
    damaged = root / "RUN_damaged" / "run_state.json"
    damaged.parent.mkdir()
    damaged.write_bytes(raw)
    if remote:
        qtbot.mouseClick(page.refresh_button, Qt.LeftButton)
        finish(qtbot, context)
    else:
        context.set_key("")
        page.page_activated()
    assert any(record["photo"] and record["photo"].job_id == job.job_id for record in page.records)
    assert damaged.read_bytes() == raw
    assert "RUN_damaged" in page.notice.text()
    assert "nepodařilo" in page.notice.text()
