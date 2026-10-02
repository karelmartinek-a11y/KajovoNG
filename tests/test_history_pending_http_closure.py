"""History Continue přebírá skutečně čekající Responses přes HTTP v novém procesu."""
import json
from unittest.mock import patch

import pytest
import requests

from kajovo.core.response_journal import ResponseJournal
from kajovo.core.run_bundle import LegacyRunAdapter
from test_delivery_http_graph import RecordingHttp, graph_files
from test_qa_qfile_http_closure import http_client
from test_runtime_end_to_end import child


class PendingHttp(RecordingHttp):
    def __init__(self, root, mode, pending=False):
        super().__init__(root, mode)
        self.pending = pending

    def request(self, method, url, **kwargs):
        path = url.split('/v1', 1)[1]
        if path.startswith('/responses/resp_'):
            row = {'method':method, 'path':path, 'body':kwargs.get('json')}
            self.calls.append(row)
            with (self.root / 'pending-get.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
            response = requests.Response()
            response.headers['Content-Type'] = 'application/json'
            response.status_code = 404 if self.pending else 200
            value = {'error':{'message':'synteticky čeká'}} if self.pending else json.loads((self.root / 'accepted-response.json').read_text(encoding="utf-8"))
            assert path.endswith(value.get('id', path.rsplit('/',1)[1]))
            response._content = json.dumps(value).encode()
            return response
        response = super().request(method,url,**kwargs)
        body = kwargs.get('json') or {}
        if self.pending and path == '/responses' and body['text']['format']['name'].endswith('1_PLAN_V2'):
            value = response.json()
            (self.root / 'accepted-response.json').write_text(json.dumps(value), encoding="utf-8")
            response._content = json.dumps({'id':value['id'], 'model':value['model'], 'status':'queued'}).encode()
        return response


class Clock:
    def __init__(self):
        self.value = 0
    def now(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


def source(root, mode):
    from change_v2_fixtures import scenario
    transport = PendingHttp(root,mode,True)
    worker, _, _ = scenario(root,mode,files=graph_files(mode),maximum_quality=True,stop_after_plan=True)
    clock = Clock()
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch('kajovo.core.runs.executor.OpenAIClient', lambda *a, **k:http_client(transport)), patch('kajovo.core.runs.executor.ResponseJournal', lambda logger,timeout:ResponseJournal(logger,timeout,clock=clock.now,sleep=clock.advance)):
        worker.run()
    assert not results and errors
    adapter = LegacyRunAdapter(worker.log.paths.run_dir)
    state = json.loads((adapter.root / 'run_state.json').read_text(encoding="utf-8"))
    assert state['status'] == 'response_pending'
    assert state['response_pending']['id'] == json.loads((root / 'accepted-response.json').read_text(encoding="utf-8"))['id']
    assert adapter.bundle.verify_integrity()['status'] == 'unsealed'
    (root / 'pending-parent.json').write_text(json.dumps({'run':str(adapter.root)}), encoding="utf-8")


def continued(root, mode):
    from PySide6.QtCore import QEventLoop, QTimer, QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.workbench import Workbench
    from kajovo.studio.history import HistoryPage
    from kajovo.studio.history_models import build_run
    from kajovo.studio.history_composer import BranchComposer
    app = QApplication([])
    transport = PendingHttp(root,mode)
    manager = Operations(None)
    context = StudioContext(AppSettings(log_dir=str(root/'LOG'),cache_dir=str(root/'cache')),manager,api_key='synthetic',client_factory=lambda *a,**k:http_client(transport))
    context.models = ['gpt-4o-mini']
    workbench = Workbench(context)
    manager.setParent(workbench)
    history = HistoryPage(context,workbench,workbench)
    adapter = LegacyRunAdapter(json.loads((root/'pending-parent.json').read_text(encoding="utf-8"))['run'])
    before = {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    def wait(predicate):
        loop,poll,limit = QEventLoop(),QTimer(),QTimer()
        poll.timeout.connect(lambda:loop.quit() if predicate() else None)
        limit.setSingleShot(True)
        limit.timeout.connect(loop.quit)
        poll.start(10)
        limit.start(30000)
        loop.exec()
        poll.stop()
        limit.stop()
        assert predicate(), [(r.terminal,r.error) for r in manager.records.values()]
    history.load_run(build_run(adapter.run_record(),steps=adapter.steps()))
    wait(lambda:history.adapter is not None and not manager.active)
    timer = QTimer()
    previews = []
    def confirm():
        dialogs = history.findChildren(BranchComposer)
        if dialogs and dialogs[-1].preview and dialogs[-1].confirm_button.isEnabled():
            previews.append(dialogs[-1].preview)
            dialogs[-1].confirm_button.click()
            timer.stop()
    timer.timeout.connect(confirm)
    timer.start(10)
    with patch('kajovo.core.runs.executor.OpenAIClient',lambda *a,**k:http_client(transport)):
        history.buttons['continue'].click()
        wait(lambda: any(r.result and isinstance(r.result,dict) and r.result.get('status') == 'plan_ready' for r in manager.records.values()) and not manager.active)
    assert len(previews) == 1
    assert previews[0].first_paid_operation == 'Převzetí již odeslané odpovědi bez nového zadání'
    child_record = next(r for r in manager.records.values() if r.identifier.startswith('RUN_'))
    target = LegacyRunAdapter(root/'LOG'/child_record.identifier)
    assert target.run_record()['parent_run_id'] == adapter.run_id
    assert target.bundle.verify_integrity()['valid']
    posts = [row['body']['text']['format']['name'] for row in transport.calls if row['path'] == '/responses']
    prefix = 'A' if mode == 'GENERATE' else 'B'
    assert posts == [prefix+'2_SPINE_V2', *[prefix+'2_FILE_SPEC_V1']*3, prefix+'2Q_QUALITY_GATE_V3'], posts
    gets = [r for r in transport.calls if r['method']=='GET' and r['path'].startswith('/responses/')]
    assert len(gets) == 1 and gets[0]['path'].endswith(json.loads((root/'accepted-response.json').read_text(encoding="utf-8"))['id'])
    assert before == {str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    for record in manager.records.values():
        if record.dialog:
            record.dialog.close()
    workbench.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)


@pytest.mark.parametrize('mode',['GENERATE','MODIFY'])
def test_history_pending_response_new_process_actual_continue_button(tmp_path,mode):
    for function in ['source','continued']:
        child(f"from pathlib import Path; import sys; from test_history_pending_http_closure import {function}; {function}(Path(sys.argv[1]), {mode!r})",tmp_path)
