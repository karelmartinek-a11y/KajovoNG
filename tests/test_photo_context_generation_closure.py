"""Qt callback neobnoví starý přehled po návratu do původního kontextu."""
import threading

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from kajovo.studio.photos import PhotosPage
from test_batch_panel_photo_boundaries import setup, finish


@pytest.mark.parametrize('page_kind', ['batches', 'photos'])
@pytest.mark.parametrize('change', ['account_aba', 'log_aba'])
@pytest.mark.parametrize('cleanup_delay', [False, True])
def test_photo_overview_late_worker_rejects_context_aba(qtbot, tmp_path, monkeypatch, page_kind, change, cleanup_delay):
    job, client, batches, context, log_dir = setup(qtbot, tmp_path)
    page = batches if page_kind == 'batches' else PhotosPage(context)
    if page_kind == 'photos':
        qtbot.addWidget(page)
    if cleanup_delay:
        original_finished = context.operations.on_finished
        monkeypatch.setattr(context.operations, 'on_finished', lambda record, callback:
                            original_finished(record, lambda: QTimer.singleShot(40, callback)))
    entered, release = threading.Event(), threading.Event()
    payload = client.list_batches.return_value
    def delayed():
        entered.set()
        assert release.wait(10)
        return payload
    client.list_batches.side_effect = delayed
    button = page.refresh_button if page_kind == 'batches' else next(
        b for b in page.findChildren(QPushButton) if b.objectName() == 'photos.jobs.refresh')
    button.click()
    try:
        qtbot.waitUntil(entered.is_set, timeout=10000)
        if change == 'account_aba':
            original = context.api_key
            context.set_key('other-account')
            context.set_key(original)
        else:
            original = context.settings.log_dir
            context.settings.log_dir = str(tmp_path/'OTHER_LOG')
            context.settings_changed.emit()
            context.settings.log_dir = original
            context.settings_changed.emit()
    finally:
        release.set()
    finish(qtbot, context, page)
    assert not (page.records if page_kind == 'batches' else page.jobs), 'Late callback revived previous context'
    assert not page.busy
    assert client.list_batches.call_count == 1
    client.create_image_batch.assert_not_called()
    # Backend dokončil pouze sledování původního jobu; žádné mazání ani submit.
    from kajovo.core.photo_batch import load_jobs
    saved = load_jobs(log_dir)
    assert len(saved) == 1 and saved[0].job_id == job.job_id
    assert saved[0].status == 'completed'
    assert saved[0].output_file_id == 'file_ready'
