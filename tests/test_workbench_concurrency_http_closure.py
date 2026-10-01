"""Deterministické dvojkliky a opožděné odpovědi skutečných QA/QFILE workerů."""

import threading

import pytest

from test_qa_qfile_http_closure import WorkflowHttp, page_fixture, settle


class BarrierHttp(WorkflowHttp):
    def __init__(self, root):
        super().__init__(root)
        self.entered, self.release = threading.Event(), threading.Event()

    def request(self, method, url, **kwargs):
        if method == 'POST' and url.endswith('/responses'):
            self.entered.set()
            assert self.release.wait(20), 'Responses bariéra nebyla uvolněna'
        return super().request(method,url,**kwargs)


@pytest.mark.parametrize('mode',['QA','QFILE'])
def test_workbench_double_click_same_request_is_one_paid_submit(qtbot, monkeypatch, tmp_path, mode):
    transport = BarrierHttp(tmp_path)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path,mode,transport)
    page.start_button.click()
    qtbot.waitUntil(transport.entered.is_set)
    page.start_button.click()
    transport.release.set()
    settle(qtbot,operations,page)
    assert len(operations.records) == 1
    assert sum(c['path'] == '/responses' for c in transport.calls) == 1


@pytest.mark.parametrize('mode',['QA','QFILE'])
@pytest.mark.parametrize('change',['prompt','account_aba','new_form'])
def test_workbench_late_http_result_remains_in_its_operation(qtbot, monkeypatch, tmp_path, mode, change):
    transport = BarrierHttp(tmp_path)
    page, operations = page_fixture(qtbot,monkeypatch,tmp_path,mode,transport)
    page.start_button.click()
    qtbot.waitUntil(transport.entered.is_set)
    if change == 'prompt':
        page.prompt.setPlainText('Nové zadání čeká na výsledek.')
    elif change == 'account_aba':
        page.context.set_key('synthetic-second')
        page.context.set_key('synthetic-offline')
    else:
        page.apply_state(page.state())
    current = {'status':'waiting','text':'Výsledek aktuálního zadání ještě není znám.'}
    page.result.set_value(current)
    before = page.state()
    transport.release.set()
    settle(qtbot,operations,page)
    record = list(operations.records.values())[-1]
    assert record.terminal in {'completed','qfile_plan_ready'} and record.result
    assert page.result.value == current
    assert page.state() == before
    assert sum(c['path'] == '/responses' for c in transport.calls) == 1


def test_main_window_close_during_http_keeps_worker_alive(qtbot,monkeypatch,tmp_path):
    from kajovo.core.config import AppSettings
    from kajovo.studio.application import create_window
    from test_qa_qfile_http_closure import http_client
    transport = BarrierHttp(tmp_path)
    window = create_window(AppSettings(log_dir=str(tmp_path/'LOG'),cache_dir=str(tmp_path/'cache')),api_key='synthetic-offline',client_factory=lambda *a,**k:http_client(transport))
    qtbot.addWidget(window)
    window.show()
    window.context.models = ['gpt-4o-mini']
    page = window.workbench
    page.apply_state({'project':'Close test','prompt':'Odpověz','mode':'QA','model':'gpt-4o-mini'})
    monkeypatch.setattr('kajovo.core.runs.executor.OpenAIClient',lambda *a,**k:http_client(transport))
    page.start_button.click()
    qtbot.waitUntil(transport.entered.is_set)
    window.close()
    visible = window.isVisible()
    transport.release.set()
    assert visible and window.operations.active
    settle(qtbot,window.operations,page)
    assert page.result.value['status'] == 'completed'
    assert sum(r['path']=='/responses' for r in transport.calls) == 1
    window.close()
    assert not window.isVisible()
