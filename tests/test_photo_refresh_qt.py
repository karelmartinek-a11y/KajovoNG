"""Nativní Qt karty musí zpřístupnit hotovou dávku i při chybě jiné úlohy."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QFrame, QPushButton

from kajovo.core import photo_batch
from kajovo.core.config import AppSettings
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from kajovo.studio.photos import PhotosPage
from test_photo_refresh_isolation import _setup
from test_photo_studio import _frozen_job
from test_photo_studio import _image_bytes
import base64
import json
from pathlib import Path


@pytest.mark.parametrize("fault", ["interrupted", "unknown", "corrupt"])
def test_completed_photo_card_remains_downloadable(qtbot, tmp_path, monkeypatch, fault):
    log_dir, source, good, client, state = _setup(tmp_path)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    _, bad = _frozen_job(bad_dir)
    bad.input_file_id = "file_interrupted"
    if fault == "unknown":
        bad.status = "submission_unknown"
    path = photo_batch.save_job(bad, log_dir) / "photo_job.json"
    if fault == "corrupt":
        path.write_text("{not valid JSON", encoding="utf-8")
    original = path.read_bytes()
    context = StudioContext(
        AppSettings(log_dir=str(log_dir), cache_dir=str(tmp_path / "cache")),
        Operations(None), api_key="offline-test", client_factory=lambda *args, **kwargs: client,
    )
    page = PhotosPage(context)
    qtbot.addWidget(page)

    def execute(title, function, receive, **kwargs):
        receive(function(client, SimpleNamespace(progress_event=Mock(), logline=Mock())))

    monkeypatch.setattr(page, "execute", execute)
    page.refresh_jobs()
    card = page.findChild(QFrame, f"photos.job.{good.job_id}")
    assert card is not None
    button = card.findChild(QPushButton, "photos.jobs.save")
    assert button is not None and button.isEnabled()
    assert path.read_bytes() == original
    if fault != "interrupted":
        assert bad.job_id in page.notice.text()
    client.create_image_batch.assert_not_called()
    client.upload_file.assert_not_called()


def test_photo_refresh_worker_and_download_worker_reach_disk(qtbot, tmp_path):
    log_dir, source, good, client, state = _setup(tmp_path)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    _, bad = _frozen_job(bad_dir)
    bad.input_file_id = "file_interrupted"
    bad.status = "submission_unknown"
    evidence = photo_batch.save_job(bad, log_dir) / "photo_job.json"
    original = evidence.read_bytes()
    png = _image_bytes()
    client.file_content.return_value = (json.dumps({
        "custom_id": good.items[0].custom_id, "error": None,
        "response": {"status_code": 200, "body": {"data": [{
            "b64_json": base64.b64encode(png).decode("ascii"),
        }]}},
    }) + "\n").encode()
    context = StudioContext(
        AppSettings(log_dir=str(log_dir), cache_dir=str(tmp_path / "cache")),
        Operations(None), api_key="offline-test", client_factory=lambda *args, **kwargs: client,
    )
    page = PhotosPage(context)
    qtbot.addWidget(page)
    page.show()
    page.refresh_jobs()
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    ready = next(job for job in page.jobs if job.job_id == good.job_id)
    assert ready.status == "completed" and ready.output_file_id == "file_ready"
    assert bad.job_id in page.notice.text()
    # Použije skutečný QThread, signál výsledku a diskový backend také při převzetí.
    page.execute("Převzetí fotografií", lambda client, task: photo_batch.download_results(
        client, ready, log_dir, progress=task.progress_event.emit), lambda job: page.load_jobs(), popup=False)
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    downloaded = next(job for job in page.jobs if job.job_id == good.job_id)
    assert downloaded.status == "downloaded"
    assert Path(downloaded.items[0].output_path).read_bytes() == png
    assert source.read_bytes() == png and evidence.read_bytes() == original
    client.create_image_batch.assert_not_called()
    client.upload_file.assert_not_called()
