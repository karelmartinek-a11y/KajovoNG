"""History Cascade přes skutečnou HTTP hranici, procesy a potvrzovací Qt akci."""
import json,os,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import pytest
from test_cascade_http_closure import definition,CascadeHttp,client_for,MODEL
from test_runtime_end_to_end import ROOT,child
SCRIPT=Path(__file__).resolve()


def produce(root,repair=False):
    from kajovo.core.cascade_pipeline import CascadeRunConfig,CascadeRunExecutor
    from kajovo.core.config import AppSettings
    from kajovo.core.runlog import RunLogger
    value=definition();transport=CascadeHttp(root,value)
    original_http=transport.request
    def http(method,url,**kwargs):
        response=original_http(method,url,**kwargs)
        if repair and url.endswith('/responses') and transport.submits==2:
            response.status_code=400;response._content=b'{"error":{"message":"Synthetic rejected decision"}}'
        return response
    transport.request=http
    worker=CascadeRunExecutor(CascadeRunConfig('History Cascade',value,'',str(root/'OUT')),AppSettings(log_dir=str(root/'LOG')),'synthetic')
    original=RunLogger.checkpoint
    def checkpoint(logger,kind,**kwargs):
        result=original(logger,kind,**kwargs)
        if kind=='cascade_step_completed':
            (root/'source.json').write_text(json.dumps({'run':str(logger.paths.run_dir),'checkpoint':result['checkpoint_id']}), encoding="utf-8")
            if not repair:os._exit(91)
        return result
    with patch('kajovo.core.cascade_pipeline.OpenAIClient',lambda *a,**k:client_for(transport)),patch.object(RunLogger,'checkpoint',checkpoint):worker.execute()
    if repair:
        assert json.loads(Path(worker.logger.state_path).read_text(encoding="utf-8"))['status']=='failed'
        return
    raise AssertionError('První dokončený krok měl proces ukončit.')


def branch(root,relation):
    from PySide6.QtCore import QEventLoop,QTimer
    from PySide6.QtWidgets import QApplication
    from kajovo.core.config import AppSettings
    from kajovo.core.run_bundle import LegacyRunAdapter
    from kajovo.core.cascade_types import CascadeDefinition
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.workbench import Workbench
    from kajovo.studio.history import HistoryPage
    from kajovo.studio.history_models import build_run
    from kajovo.studio.history_composer import BranchComposer
    app=QApplication([]);info=json.loads((root/'source.json').read_text(encoding="utf-8"));adapter=LegacyRunAdapter(info['run'])
    before={str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    state=json.loads((adapter.root/'run_state.json').read_text(encoding="utf-8"));value=CascadeDefinition.from_dict(state['cascade_definition'])
    transport=CascadeHttp(root,value)
    original=transport.request
    def http(method,url,**kwargs):
        response=original(method,url,**kwargs)
        if url.endswith('/responses'):
            data=response.json();data['id']='resp_new_branch_'+str(transport.submits);response._content=json.dumps(data).encode()
        return response
    transport.request=http
    operations=Operations(None);context=StudioContext(AppSettings(log_dir=str(root/'LOG'),cache_dir=str(root/'cache')),operations,api_key='synthetic',client_factory=lambda *a,**k:client_for(transport));context.models=[MODEL]
    workbench=Workbench(context);operations.setParent(workbench);history=HistoryPage(context,workbench,workbench)
    def wait(predicate):
        loop=QEventLoop();timer=QTimer();guard=QTimer();guard.setSingleShot(True);guard.timeout.connect(loop.quit)
        timer.timeout.connect(lambda:loop.quit() if predicate() else None);timer.start(10);guard.start(30000);loop.exec();timer.stop();guard.stop();assert predicate(),(history.notice.text(),confirmed if 'confirmed' in locals() else 'before dialog',[(r.title,r.terminal,r.error,str(r.result)[:200]) for r in operations.records.values()])
    history.load_run(build_run(adapter.run_record(),steps=adapter.steps()));wait(lambda:history.adapter is not None and not operations.active)
    assert history.buttons[relation].isEnabled(),history.decisions[relation]
    confirmed=[];timer=QTimer()
    def accept():
        dialogs=history.findChildren(BranchComposer)
        if not dialogs:return
        composer=dialogs[-1];idx=composer.checkpoint.findData(info['checkpoint'])
        assert idx>=0
        if composer.checkpoint.currentIndex()!=idx:composer.checkpoint.setCurrentIndex(idx);return
        if composer.preview and composer.confirm_button.isEnabled():
            if relation=='repair':composer.instruction.setPlainText('Dokonči zbývající kroky beze změny doloženého základu.')
            confirmed.append(composer.preview);composer.confirm_button.click();timer.stop()
    timer.timeout.connect(accept);timer.start(10)
    with patch('kajovo.core.cascade_pipeline.OpenAIClient',lambda *a,**k:client_for(transport)):
        history.buttons[relation].click();wait(lambda:not operations.active and any(isinstance(r.result,dict) and bool(r.result.get('executed_step_ids')) for r in operations.records.values()))
    assert len(confirmed)==1
    record=next(r for r in operations.records.values() if isinstance(r.result,dict) and bool(r.result.get('executed_step_ids')))
    assert record.terminal=='completed' and record.identifier!=adapter.run_id
    target=LegacyRunAdapter(root/'LOG'/record.identifier)
    assert target.run_record()['parent_run_id']==adapter.run_id
    assert target.run_record()['continued_from_checkpoint_id']==info['checkpoint']
    assert target.bundle.verify_integrity()['valid']
    assert transport.submits==2
    final=json.loads((target.root/'run_state.json').read_text(encoding="utf-8"))
    assert final['status']=='completed'
    assert set(final['cascade_runtime']['executed_step_ids'])=={value.steps[i].id for i in [0,1,3]}
    assert before=={str(p.relative_to(adapter.root)):p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    for r in operations.records.values():
        if r.dialog:r.dialog.close()
    workbench.close();app.processEvents()


@pytest.mark.parametrize('relation',['continue','rerun','repair'])
def test_history_cascade_checkpoint_new_process_http_and_real_confirmation(tmp_path,relation):
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'tests')]))
    first=subprocess.run([sys.executable,'-c',f"import runpy; from pathlib import Path; import sys; runpy.run_path({str(SCRIPT)!r})['produce'](Path(sys.argv[1]),{relation=='repair'!r})",str(tmp_path)],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=60)
    assert first.returncode==(0 if relation=='repair' else 91),first.stdout+first.stderr
    child(f"import runpy; from pathlib import Path; import sys; runpy.run_path({str(SCRIPT)!r})['branch'](Path(sys.argv[1]),{relation!r})",tmp_path)
    rows=[json.loads(line) for line in (tmp_path/'cascade-http.jsonl').read_text(encoding="utf-8").splitlines()]
    assert sum(row['path']=='/responses' for row in rows)==(4 if relation=='repair' else 3)
