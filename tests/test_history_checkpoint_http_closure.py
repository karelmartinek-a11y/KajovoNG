"""Historie přebírá skutečné přípravné checkpointy přes UI a nový proces."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kajovo.core.run_bundle import LegacyRunAdapter
from test_delivery_http_graph import RecordingHttp, graph_files, expected_content
from test_qa_qfile_http_closure import http_client
from test_runtime_end_to_end import child


def source_process(root, mode, complete=False):
    from change_v2_fixtures import scenario
    transport = RecordingHttp(root, mode)
    worker, _, _ = scenario(root, mode, maximum_quality=True, files=graph_files(mode), stop_after_plan=not complete)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch('kajovo.core.runs.executor.OpenAIClient', lambda *a, **k: http_client(transport)):
        worker.run()
    assert not errors and results[0]['status'] == ('files_complete_unverified' if complete else 'plan_ready'), errors
    if complete:
        from kajovo.core.orchestration.publish import publish_staged_run
        publish_staged_run(worker.log.paths.run_dir)
    adapter = LegacyRunAdapter(worker.log.paths.run_dir)
    assert adapter.bundle.verify_integrity()['valid']
    assert complete or not any(row['body']['text']['format']['name'] == 'FILE_CONTENT_V1' for row in transport.calls if row['path'] == '/responses')
    (root / 'history-source.json').write_text(json.dumps({'run':str(adapter.root),'out':worker.cfg.out_dir}))


def branch_process(root, mode, suffix, relation="continue", partial=False, crashed=False):
    from PySide6.QtCore import QEventLoop, QTimer, QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.workbench import Workbench
    from kajovo.studio.history import HistoryPage
    from kajovo.studio.history_composer import BranchComposer
    from kajovo.studio.history_models import build_run
    app = QApplication([])
    transport = RecordingHttp(root, mode)
    factory = lambda *a, **k: http_client(transport)
    manager = Operations(None)
    settings = AppSettings(log_dir=str(root / 'LOG'), cache_dir=str(root / 'cache'))
    context = StudioContext(settings, manager, api_key='synthetic', client_factory=factory)
    context.models = ['gpt-4o-mini']
    workbench = Workbench(context)
    manager.setParent(workbench)
    history = HistoryPage(context,workbench,workbench)
    info = json.loads((root / 'history-source.json').read_text())
    adapter = LegacyRunAdapter(info['run'])
    before = {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    checkpoint_type = suffix if suffix in {'plan_ready','files_downloaded_validated','input_ready'} else ('A' if mode == 'GENERATE' else 'B') + suffix
    checkpoint = next(r for r in adapter.checkpoints() if r['checkpoint_type'] == checkpoint_type)
    def wait(predicate):
        loop, poll, watchdog = QEventLoop(), QTimer(), QTimer()
        poll.timeout.connect(lambda:loop.quit() if predicate() else None)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        poll.start(10)
        watchdog.start(30000)
        loop.exec()
        poll.stop()
        watchdog.stop()
        assert predicate(), [(r.terminal,r.error) for r in manager.records.values()]
    history.load_run(build_run(adapter.run_record(),steps=adapter.steps()))
    wait(lambda: history.adapter is not None and not manager.active)
    if relation == 'continue' and ((partial and not crashed) or suffix == 'files_downloaded_validated'):
        assert not history.buttons['continue'].isEnabled()
        assert transport.calls == []
        assert before == {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
        workbench.close()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        return
    assert history.buttons[relation].isEnabled(), history.decisions[relation]
    click_timer = QTimer()
    confirmed = []
    def confirm():
        dialogs = history.findChildren(BranchComposer)
        if not dialogs:
            return
        composer = dialogs[-1]
        idx = composer.checkpoint.findData(checkpoint['checkpoint_id'])
        assert idx >= 0
        if composer.checkpoint.currentIndex() != idx:
            composer.checkpoint.setCurrentIndex(idx)
            return
        if composer.preview and composer.confirm_button.isEnabled():
            if relation == 'repair':
                composer.instruction.setPlainText('Dokonči pouze chybějící soubor; zachovej doložené bytes.')
            confirmed.append(composer.preview)
            composer.confirm_button.click()
            click_timer.stop()
    click_timer.timeout.connect(confirm)
    click_timer.start(10)
    with patch('kajovo.core.runs.executor.OpenAIClient',factory):
        history.buttons[relation].click()
        wait(lambda: not manager.active and any(r.result and isinstance(r.result,dict) and r.result.get('status') in {'files_complete_unverified','plan_ready'} for r in manager.records.values()))
    assert len(confirmed) == 1
    child_record = next(r for r in manager.records.values() if r.identifier.startswith('RUN_') and r.identifier != adapter.run_id)
    assert child_record.identifier != adapter.run_id
    target = LegacyRunAdapter(root / 'LOG' / child_record.identifier)
    assert target.run_record()['parent_run_id'] == adapter.run_id
    assert target.run_record()['continued_from_checkpoint_id'] == checkpoint['checkpoint_id']
    assert target.bundle.verify_integrity()['valid']
    posts = [c['body'] for c in transport.calls if c['path'] == '/responses']
    names = [p['text']['format']['name'] for p in posts]
    if partial or suffix in {'plan_ready','files_downloaded_validated'}:
        assert child_record.terminal == 'files_complete_unverified', child_record.error
        assert names == (['FILE_CONTENT_V1'] * (2 if crashed else 1) if partial else ['FILE_CONTENT_V1'] * 3 if relation == 'rerun' or suffix == 'plan_ready' else []), names
        from kajovo.core.orchestration.publish import publish_staged_run
        publish_staged_run(target.root)
        for path in ['seed.txt','middle.txt','final.txt']:
            assert (Path(info['out']) / path).read_bytes() == expected_content(mode,path).encode()
    else:
        assert child_record.terminal == 'plan_ready', child_record.error
        assert 'FILE_CONTENT_V1' not in names
        first = {'0R':('A' if mode == 'GENERATE' else 'B')+'1_PLAN_V2',
                 '1':('A' if mode == 'GENERATE' else 'B')+'2_SPINE_V2',
                 '2':('A' if mode == 'GENERATE' else 'B')+'2Q_QUALITY_GATE_V3'}
        if suffix in first:
            assert names[0] == first[suffix], names
        elif suffix == '2Q':
            assert posts == []
            assert confirmed[0].first_paid_operation.startswith('Bez nového requestu'), confirmed[0]
    assert before == {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    for r in manager.records.values():
        if r.dialog:
            r.dialog.close()
    workbench.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)


@pytest.mark.parametrize('mode',['GENERATE','MODIFY'])
@pytest.mark.parametrize('suffix',['0R','1','2','2Q','plan_ready'])
def test_history_preparation_checkpoint_new_process_actual_ui_http(tmp_path, mode, suffix):
    child("from pathlib import Path; import sys; from test_history_checkpoint_http_closure import source_process; source_process(Path(sys.argv[1]), " + repr(mode) + ")",tmp_path)
    child("from pathlib import Path; import sys; from test_history_checkpoint_http_closure import branch_process; branch_process(Path(sys.argv[1]), " + repr(mode) + ", " + repr(suffix) + ")",tmp_path)


@pytest.mark.parametrize('mode',['GENERATE','MODIFY'])
@pytest.mark.parametrize('relation',['continue','rerun'])
def test_history_validated_files_checkpoint_actual_ui_new_process(tmp_path,mode,relation):
    child(f"from pathlib import Path; import sys; from test_history_checkpoint_http_closure import source_process; source_process(Path(sys.argv[1]), {mode!r}, True)",tmp_path)
    child(f"from pathlib import Path; import sys; from test_history_checkpoint_http_closure import branch_process; branch_process(Path(sys.argv[1]), {mode!r}, 'files_downloaded_validated', {relation!r})",tmp_path)
