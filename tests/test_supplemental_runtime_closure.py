from native_provider_fixtures import native_fixture
"""Qt a HTTP průchody s kontrolou bytes, identity a viditelných stavů."""
import io,json,zipfile
from pathlib import Path
import pytest
from kajovo.core.cascade_types import CascadeDefinition,CascadeStep,CascadeOutput
from kajovo.core.config import AppSettings
from kajovo.core.run_bundle import LegacyRunAdapter
from test_cascade_document_http_closure import DocumentHttp,MODEL
from test_cascade_http_closure import client_for
from test_qa_qfile_http_closure import WorkflowHttp,page_fixture,settle
from kajovo.studio.history_launcher import HistoryBranchLauncher
from types import SimpleNamespace


def docx_bytes():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Přesný dokument</w:t></w:r></w:p></w:body></w:document>')
    return stream.getvalue()


def test_cascade_docx_ui_http_identity_and_actual_document_bytes(qtbot, monkeypatch, tmp_path):
    raw=docx_bytes()
    (tmp_path/'artifact.zip').write_bytes(raw)
    output=CascadeOutput(kind='file',file_type='docx',file_name='obsah.docx')
    definition=CascadeDefinition('DOCX',steps=[CascadeStep(title='Dokument',model=MODEL,input_text='Vytvoř dokument',deterministic=True,outputs=[output])])
    transport=DocumentHttp(tmp_path,output)
    from kajovo.studio.cascades import CascadesPage
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from PySide6.QtWidgets import QPushButton
    from shiboken6 import isValid
    operations=Operations(None)
    context=StudioContext(AppSettings(log_dir=str(tmp_path/'LOG'),cache_dir=str(tmp_path/'cache')),operations,api_key='synthetic')
    context.models=[MODEL]
    page=CascadesPage(context);operations.setParent(page);qtbot.addWidget(page)
    page.definition,page.current_id=definition,None
    page.name.setText(definition.name);page.project.setText('DOCX');page.output.setText(str(tmp_path/'OUT'));page.draw_steps()
    monkeypatch.setattr('kajovo.core.cascade_pipeline.OpenAIClient',lambda *a,**k:client_for(transport))
    page.findChild(QPushButton,'cascade.start').click()
    qtbot.waitUntil(lambda:bool(operations.records) and not operations.active,timeout=30000)
    record=next(iter(operations.records.values()))
    assert record.terminal=='completed' and record.result and not record.error
    assert not isValid(record.worker) or not record.worker.isRunning()
    bundle=LegacyRunAdapter(tmp_path/'LOG'/record.identifier).bundle
    record.dialog.close()
    destination=tmp_path/'OUT/obsah.docx'
    assert destination.read_bytes()==raw
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist())=={'[Content_Types].xml','_rels/.rels','word/document.xml'}
        assert 'Přesný dokument' in archive.read('word/document.xml').decode()
    assert bundle.verify_integrity()['valid']
    assert sum(row['path']=='/responses' for row in transport.calls)==2
    assert sum(row['path']=='/files' and row['method']=='POST' for row in transport.calls)==1
    assert any(row['path']=='/containers/cntr_one/files/cfile_one/content' for row in transport.calls)


@pytest.mark.parametrize('fault',['checkpoint','database'])
def test_history_corrupt_evidence_blocks_before_worker_and_submit(qtbot,monkeypatch,tmp_path,fault):
    page,operations=page_fixture(qtbot,monkeypatch,tmp_path,'QA',WorkflowHttp(tmp_path))
    page.start_button.click();settle(qtbot,operations,page)
    record=next(iter(operations.records.values()))
    adapter=LegacyRunAdapter(tmp_path/'LOG'/record.identifier)
    checkpoint=next(row for row in adapter.checkpoints() if row['checkpoint_type']=='input_ready')
    launcher=HistoryBranchLauncher(SimpleNamespace())
    if fault=='database':
        (adapter.root.parent/'orchestration.sqlite3').write_bytes(b'Synthetic broken SQLite')
        message='Provider evidenci'
    else:
        path=adapter.root/'checkpoints'/f"{checkpoint['checkpoint_id']}.json"
        data=json.loads(path.read_text());data['state_snapshot']['ui_state']['prompt']='foreign input'
        path.write_text(json.dumps(data));message='Zdrojový Run Bundle neprošel kontrolou integrity'
    with pytest.raises(ValueError,match=message):launcher.preview(adapter,checkpoint['checkpoint_id'],'rerun')
    assert len(operations.records)==1
    rows=[json.loads(line) for line in (tmp_path/'workflow-http.jsonl').read_text().splitlines()]
    assert sum(row['path']=='/responses' for row in rows)==1


def test_old_catalog_keeps_cache_provenance_until_ui_http_refresh(qtbot, monkeypatch, tmp_path):
    from test_settings_http_closure import fixture
    from kajovo.studio.application import ModelsPage
    page, context, _, transport = fixture(qtbot, monkeypatch, tmp_path)
    context._model_cache.save(context.api_key,[{'id':'gpt-4o-mini'}])
    path=context._model_cache.path
    data=json.loads(path.read_text());data['fetched_at']=1.0
    path.write_text(json.dumps(data));context._load_cached_models()
    from kajovo.studio.workbench import Workbench
    from PySide6.QtWidgets import QPushButton
    workbench=Workbench(context);qtbot.addWidget(workbench)
    models=ModelsPage(context,workbench);qtbot.addWidget(models);models.render()
    assert models.listing.count()==1
    assert context.models_source=='cache' and context.models_fetched_at==1.0
    assert context.models==['gpt-4o-mini']
    assert context.model_details('gpt-4o-mini')['catalog_source']=='cache'
    assert transport.calls==[]
    models.findChild(QPushButton,"models.refresh").click()
    qtbot.waitUntil(lambda:not context.operations.active)
    assert context.models_source=='live' and context.models_fetched_at>1.0
    assert [row['path'] for row in transport.calls]==['/models']
    for record in context.operations.records.values():
        from shiboken6 import isValid
        if isValid(record.dialog): record.dialog.close()


@pytest.mark.parametrize('index_status',['in_progress','failed'])
def test_nonusable_store_blocks_qa_before_http_submit(qtbot,monkeypatch,tmp_path,index_status):
    import requests
    class StoreHttp(WorkflowHttp):
        def request(self,method,url,**kwargs):
            path=url.split('/v1',1)[1]
            if path=='/vector_stores/vs_nonusable':
                self.calls.append({'method':method,'path':path,'body':kwargs.get('json')})
                counts={'completed':0,'in_progress':int(index_status=='in_progress'),'failed':int(index_status=='failed'),'cancelled':0,'total':1}
                value={'id':'vs_nonusable','status':index_status,'file_counts':counts}
                response=requests.Response();response.status_code=200;response.headers['Content-Type']='application/json';response._content=json.dumps(native_fixture(method, path, value, kwargs.get("json") or kwargs.get("data"))).encode();return response
            return super().request(method,url,**kwargs)
    transport=StoreHttp(tmp_path)
    page,operations=page_fixture(qtbot,monkeypatch,tmp_path,'QA',transport)
    page.context.stores=['vs_nonusable']
    page.start_button.click();settle(qtbot,operations,page)
    record=next(iter(operations.records.values()))
    assert record.terminal=='failed' and record.error and not record.result
    assert not any(row['path']=='/responses' for row in transport.calls)
    assert not page.result.value


@pytest.mark.parametrize('image_format',['PNG','JPEG','invalid'])
def test_cascade_image_ui_http_format_and_final_state(qtbot,monkeypatch,tmp_path,image_format):
    from test_cascade_image_http_closure import ImageHttp,image_bytes
    from test_cascade_http_closure import MODEL
    from kajovo.studio.cascades import CascadesPage
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from PySide6.QtWidgets import QPushButton
    from shiboken6 import isValid
    raw=b'not an image' if image_format=='invalid' else image_bytes(image_format)
    output=CascadeOutput(kind='file',file_type='png',file_name='výsledek.png')
    definition=CascadeDefinition('Obraz Qt HTTP',steps=[CascadeStep(title='Obraz',model=MODEL,input_text='Vyrob obraz',deterministic=True,outputs=[output])])
    transport=ImageHttp(output,raw);operations=Operations(None)
    context=StudioContext(AppSettings(log_dir=str(tmp_path/'LOG'),cache_dir=str(tmp_path/'cache')),operations,api_key='synthetic');context.models=[MODEL]
    page=CascadesPage(context);operations.setParent(page);qtbot.addWidget(page)
    page.definition,page.current_id=definition,None;page.name.setText(definition.name);page.project.setText('PNG Qt');page.output.setText(str(tmp_path/'OUT'));page.draw_steps()
    monkeypatch.setattr('kajovo.core.cascade_pipeline.OpenAIClient',lambda *a,**k:client_for(transport))
    page.findChild(QPushButton,'cascade.start').click()
    qtbot.waitUntil(lambda:bool(operations.records) and not operations.active,timeout=30000)
    record=next(iter(operations.records.values()));valid=image_format=='PNG'
    assert record.terminal==('completed' if valid else 'failed')
    assert not isValid(record.worker) or not record.worker.isRunning()
    assert bool(record.result) is valid
    assert sum(row['path']=='/images/generations' for row in transport.calls)==1
    assert sum(row['path']=='/files' and row['method']=='POST' for row in transport.calls)==int(valid)
    destination=tmp_path/'OUT/výsledek.png'
    if valid:
        assert destination.read_bytes()==raw
        assert LegacyRunAdapter(tmp_path/'LOG'/record.identifier).bundle.verify_integrity()['valid']
    else:assert not destination.exists()
    record.dialog.close()


def test_batch_panel_photo_http_refresh_against_download_preserves_bytes(qtbot,monkeypatch,tmp_path):
    import threading
    from PySide6.QtCore import Qt
    from kajovo.core import photo_batch
    from kajovo.studio.batches import BatchesPage
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from test_photo_studio import _frozen_job,_image_bytes
    from test_runtime_end_to_end import PhotoTransport
    from test_qa_qfile_http_closure import http_client
    class PanelTransport(PhotoTransport):
        def __init__(self,*args):super().__init__(*args);self.wire_calls=[]
        def request(self,method,url,**kwargs):
            self.wire_calls.append({'method':method,'url':url,'body':kwargs.get('json')})
            return super().request(method,url.split('?')[0],**kwargs)
    _,job=_frozen_job(tmp_path);transport=PanelTransport(tmp_path,False);client=http_client(transport)
    log=tmp_path/'LOG';photo_batch.prepare_and_submit(client,job,log)
    assert transport.calls.count(('POST','/batches'))==1
    transport.completed=True;transport.calls=[];transport.wire_calls=[]
    operations=Operations(None);context=StudioContext(AppSettings(log_dir=str(log),cache_dir=str(tmp_path/'cache')),operations,api_key='synthetic',client_factory=lambda *a,**k:client)
    page=BatchesPage(context);operations.setParent(page);qtbot.addWidget(page)
    entered,release=threading.Event(),threading.Event();original=photo_batch.load_jobs
    def load(*a,**k):
        rows=original(*a,**k);entered.set();assert release.wait(10);return rows
    monkeypatch.setattr(photo_batch,'load_jobs',load)
    qtbot.mouseClick(page.refresh_button,Qt.LeftButton)
    try:
        qtbot.waitUntil(entered.is_set)
        photo_batch.download_results(client,job,log)
        assert job.status=='downloaded'
    finally:release.set()
    qtbot.waitUntil(lambda:not operations.active,timeout=30000)
    stored=original(log)[0]
    assert stored.status=='downloaded' and stored.items[0].status=='downloaded'
    assert Path(stored.items[0].output_path).read_bytes()==_image_bytes()
    assert page.records[0]['photo'].status=='downloaded'
    assert all(method=='GET' for method,path in transport.calls)
    assert ('GET','/files/file_output/content') in transport.calls
    assert any(row['method']=='GET' and '/batches?limit=' in row['url'] for row in transport.wire_calls)
    (tmp_path/'exact-wire-calls.json').write_text(json.dumps(transport.wire_calls))
    for record in operations.records.values():record.dialog.close()


def test_converter_ambiguous_cp1250_keeps_original_and_backup(qtbot,tmp_path):
    from kajovo.studio.converter import ConverterWindow
    import zipfile
    page=ConverterWindow();qtbot.addWidget(page)
    source=tmp_path/'zdroj';source.mkdir();backup=tmp_path/'záloha';backup.mkdir()
    text='Příliš žluťoučký kůň úpěl ďábelské ódy.\r\n'*20
    raw=text.encode('cp1250');target=source/'český text.txt';target.write_bytes(raw)
    page.paths[0].setText(str(source));page.backup.setText(str(backup));page.start_button.click()
    qtbot.waitUntil(lambda:bool(page.operations.records) and not page.operations.active,timeout=30000)
    record=next(iter(page.operations.records.values()))
    assert record.terminal=='completed' and 'Hotovo.' in page.result.toPlainText()
    assert target.read_bytes()==raw
    assert "Opravené soubory: 0" in page.result.toPlainText()
    log=next(backup.rglob("*.jsonl"))
    events=[json.loads(line) for line in log.read_text().splitlines()]
    assert any(row["event"]=="file_skipped_encoding" and "není validní UTF-8" in row["reason"] for row in events)
    with zipfile.ZipFile(next(backup.rglob('*.zip'))) as archive:assert archive.read('český text.txt')==raw
    record.dialog.close()
