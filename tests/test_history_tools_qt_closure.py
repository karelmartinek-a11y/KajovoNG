"""Export a Clone skutečné evidence přes UI, Qt worker a bezpečné artefakty."""

import hashlib
import json
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.studio.history import HistoryPage
from kajovo.studio.history_models import build_run
from test_qa_qfile_http_closure import WorkflowHttp, page_fixture, settle


def selected(qtbot, monkeypatch, tmp_path):
    transport = WorkflowHttp(tmp_path)
    workbench, operations = page_fixture(qtbot,monkeypatch,tmp_path,'QA',transport)
    workbench.start_button.click()
    settle(qtbot,operations,workbench)
    source = list(operations.records.values())[-1]
    adapter = LegacyRunAdapter(tmp_path/'LOG'/source.identifier)
    history = HistoryPage(workbench.context,workbench,workbench)
    qtbot.addWidget(history)
    history.load_run(build_run(adapter.run_record(),steps=adapter.steps()))
    qtbot.waitUntil(lambda: history.adapter is not None and not operations.active,timeout=30000)
    return history, workbench, operations, adapter


def finished(qtbot, operations):
    qtbot.waitUntil(lambda: not operations.active,timeout=30000)
    for r in operations.records.values():
        if r.dialog:
            qtbot.addWidget(r.dialog)
            r.dialog.close()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)


@pytest.mark.parametrize('fault',['','write','symlink'])
def test_history_export_actual_action_checks_content_and_preserves_source(qtbot, monkeypatch, tmp_path, fault):
    history, _, operations, adapter = selected(qtbot,monkeypatch,tmp_path)
    original = {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    target = tmp_path/'export.zip'
    target.write_bytes(b'previous export')
    monkeypatch.setattr('kajovo.studio.history.get_save_file_name',lambda *a:(str(target),''))
    if fault == 'write':
        real_replace = __import__('os').replace
        def fail(source, destination):
            if Path(destination) == target:
                raise OSError('syntetická chyba ZIP replace')
            return real_replace(source,destination)
        monkeypatch.setattr('kajovo.studio.history_artifacts.os.replace',fail)
    elif fault == 'symlink':
        outside = tmp_path/'foreign.txt'
        outside.write_bytes(b'foreign')
        (adapter.root/'foreign-link.txt').symlink_to(outside)
    history.buttons['bundle_export'].click()
    finished(qtbot,operations)
    record = list(operations.records.values())[-1]
    assert record.terminal == ('failed' if fault else 'completed'), record.error
    assert original == {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file() and not p.is_symlink()}
    if fault:
        assert target.read_bytes() == b'previous export'
        assert not list(tmp_path.glob('.run-export-*.zip'))
    else:
        with zipfile.ZipFile(target) as archive:
            assert archive.testzip() is None
            manifest = json.loads(archive.read(adapter.run_id+'_REDACTED/DERIVED_REDACTED_EXPORT.json'))
            assert manifest['source_run_id'] == adapter.run_id
            for row in manifest['files']:
                binary = archive.read(adapter.run_id+'_REDACTED/'+row['path'])
                assert hashlib.sha256(binary).hexdigest() == row['sha256']
                assert len(binary) == row['bytes']
            answer = next(row for row in manifest['files'] if 'QA_answer' in row['path'])
            assert json.loads(archive.read(adapter.run_id+'_REDACTED/'+answer['path']))['answer'] == 'Doložená odpověď.'
        # Podporovaný import ZIP jako evidence je pouze rozbalení a čtení odvozených bytes.
        # Export výslovně nenahrazuje kanonický bundle a není zdrojem resume oprávnění.
        assert manifest['kind'] == 'derived_redacted_run_export'


def test_history_export_callback_does_not_overwrite_new_selection(qtbot, monkeypatch, tmp_path):
    history, _, operations, adapter = selected(qtbot,monkeypatch,tmp_path)
    entered, release = threading.Event(),threading.Event()
    from kajovo.studio.history_artifacts import export_run_bundle
    def block(source,target):
        result = export_run_bundle(source,target)
        entered.set()
        assert release.wait(20), 'Export bariéra nebyla uvolněna'
        return result
    monkeypatch.setattr('kajovo.studio.history_artifacts.export_run_bundle',block)
    monkeypatch.setattr('kajovo.studio.history.get_save_file_name',lambda *a:(str(tmp_path/'export.zip'),''))
    history.buttons['bundle_export'].click()
    qtbot.waitUntil(entered.is_set)
    history.load_run(build_run(adapter.run_record(),steps=adapter.steps()))
    qtbot.waitUntil(lambda:history.adapter is not None,timeout=30000)
    history.notice.setText('Nový výběr čeká na akci.')
    release.set()
    finished(qtbot,operations)
    assert history.notice.text() == 'Nový výběr čeká na akci.'
    assert (tmp_path/'export.zip').is_file()


@pytest.mark.parametrize('change',[False,True])
def test_history_clone_actual_worker_source_preserved_and_revision_guard(qtbot,monkeypatch,tmp_path,change):
    history, workbench, operations, adapter = selected(qtbot,monkeypatch,tmp_path)
    original = {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    entered, release = threading.Event(),threading.Event()
    from kajovo.studio.history import read_state
    def blocked(root):
        result = read_state(root)
        entered.set()
        assert release.wait(20), 'Clone bariéra nebyla uvolněna'
        return result
    with patch('kajovo.studio.history.read_state',blocked):
        history.buttons['clone'].click()
        qtbot.waitUntil(entered.is_set)
        if change:
            workbench.prompt.setPlainText('Nové zadání nesmí klon přepsat.')
        release.set()
        finished(qtbot,operations)
    assert original == {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    assert workbench.widgets['response_id'].text() == ('' if not change else 'resp_qa_answer_v2')
    if change:
        assert workbench.prompt.toPlainText() == 'Nové zadání nesmí klon přepsat.'
        assert 'klon nebyl použit' in history.notice.text()
    else:
        assert workbench.pending_lineage == {'source_run_id':adapter.run_id,'relation_type':'clone'}
        assert workbench.prompt.toPlainText() == 'Odpověz z tohoto zadání.'
