"""QFILE publish přes dosažitelnou History akci, bez nového provider submitu."""
import threading
import pytest
from PySide6.QtCore import Qt
from test_qa_qfile_http_closure import WorkflowHttp,page_fixture,settle
from kajovo.studio.history import HistoryPage

@pytest.mark.parametrize('change',[False,True])
def test_qfile_history_actual_publish_button_and_changed_context(qtbot,monkeypatch,tmp_path,change):
    transport=WorkflowHttp(tmp_path);page,operations=page_fixture(qtbot,monkeypatch,tmp_path,'QFILE',transport)
    page.start_button.click();settle(qtbot,operations,page)
    page.start_button.click();settle(qtbot,operations,page)
    run=list(operations.records.values())[-1].identifier
    history=HistoryPage(page.context,page,page);qtbot.addWidget(history)
    history.resize(1200,900);history.show();history.refresh()
    qtbot.waitUntil(lambda:history.model.rowCount()>0 and not operations.active,timeout=30000)
    def select_run():
        row=next(i for i in range(history.model.rowCount()) if history.model.run_at(i).run_id==run)
        index=history.model.index(row,0);history.tracks.scrollTo(index)
        qtbot.mouseClick(history.tracks.viewport(),Qt.LeftButton,pos=history.tracks.visualRect(index).center())
        qtbot.waitUntil(lambda:history.adapter is not None and not operations.active,timeout=30000)
    select_run()
    assert history.buttons['publish_staged'].isEnabled()
    entered,release=threading.Event(),threading.Event()
    import kajovo.studio.history as module
    original=module.publish_staged_run
    def publish(root):
        entered.set();assert release.wait(10);return original(root)
    monkeypatch.setattr(module,'publish_staged_run',publish)
    before=page.state();value=page.result.value
    history.buttons['publish_staged'].click()
    try:
        qtbot.waitUntil(entered.is_set)
        worker=operations.records["publish.staged:"+run].worker
        history.buttons["publish_staged"].click()
        assert operations.records["publish.staged:"+run].worker is worker
        if change:
            page.prompt.setPlainText('Nový kontext čeká.')
            page.result.set_value({'status':'waiting','text':'Aktuální výsledek není znám.'})
            before=page.state();value=page.result.value
    finally:release.set()
    qtbot.waitUntil(lambda:not operations.active,timeout=30000)
    selected=next(history.model.run_at(i) for i in range(history.model.rowCount()) if history.model.run_at(i).run_id==run)
    assert selected.status.key=='completed_unverified'
    select_run()
    assert history._state['status']=='completed_unverified'
    record=operations.records['publish.staged:'+run]
    assert record.terminal=='completed' and record.result['publish_report']['status']=='committed'
    assert (tmp_path/'OUT/výsledek/navrh.md').read_bytes()=='# Výsledek\nPřesné bytes.\n'.encode()
    assert history._state['published_files'] and not history.buttons['publish_staged'].isEnabled()
    assert history.adapter.bundle.verify_integrity()['valid']
    assert page.state()==before and page.result.value==value
    assert sum(row['path']=='/responses' for row in transport.calls)==2
    for row in operations.records.values():
        if row.dialog:row.dialog.close()
