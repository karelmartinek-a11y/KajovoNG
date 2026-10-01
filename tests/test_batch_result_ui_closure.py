"""Stažení dávky hlásí skutečný lokální výsledek z produkčního workeru."""
import json

import pytest
import requests

from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.runlog import RunLogger
from kajovo.studio.batches import BatchesPage
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations


@pytest.mark.parametrize('remote_status,expected', [('completed', 'partial'), ('in_progress', 'batch_pending'), ('completed', 'files_complete_unverified')])
def test_download_action_does_not_announce_unaccepted_result(qtbot, tmp_path, remote_status, expected):
    logger = RunLogger(str(tmp_path/'LOG'), 'RUN_BATCH_NOTICE', 'Dávka')
    logger.update_state({'batch_id': 'batch_notice', 'status': 'batch_pending',
                         'out_dir': str(tmp_path/'OUT')})
    calls = []
    def client_factory(*args, **kwargs):
        client = OpenAIClient('synthetic-offline', base_url='https://offline.invalid/v1')
        client._sdk = None
        def request(method, url, **options):
            assert method == 'GET'
            path = url.split('/v1', 1)[1]
            calls.append((method, path))
            response = requests.Response()
            response.status_code = 200
            if path == '/batches/batch_notice':
                value = {'id': 'batch_notice', 'status': remote_status,
                         'output_file_id': 'file_bad' if remote_status == 'completed' else None}
                response.headers['Content-Type'] = 'application/json'
                response._content = json.dumps(value).encode()
            elif path == '/files/file_bad/content':
                response.headers['Content-Type'] = 'application/octet-stream'
                if expected == 'partial':
                    response._content = '{poškozený JSONL}\n'.encode()
                else:
                    value = {'custom_id': 'RUN_BATCH_NOTICE_C1', 'response': {'status_code': 200, 'body': {
                        'status': 'completed', 'output_text': json.dumps({
                            'contract': 'C_FILES_ALL', 'root': 'src',
                            'files': [{'path': 'hello.txt', 'purpose': 'Výsledek', 'content': 'Přesný výsledek\n'}],
                            'project': {'name': 'Test', 'target_os': 'Windows', 'runtime': 'text', 'language': 'text'},
                            'build_run': {'prerequisites': [], 'commands': [], 'verification': []}, 'notes': [],
                        })}}}
                    response._content = (json.dumps(value) + '\n').encode()
            else:
                raise AssertionError(path)
            return response
        client.session.request = request
        return client
    operations = Operations(None)
    context = StudioContext(AppSettings(log_dir=str(tmp_path/'LOG'), cache_dir=str(tmp_path/'cache')),
                            operations, api_key='synthetic-offline', client_factory=client_factory)
    page = BatchesPage(context)
    qtbot.addWidget(page)
    operations.setParent(page)
    page.records = [{'id': 'batch_notice', 'remote': {'id': 'batch_notice', 'status': 'completed',
                       'output_file_id': 'file_bad'}, 'state': {}, 'run_dir': str(logger.paths.run_dir),
                     'kind': 'GENERATE', 'photo': None, 'started_at': 1}]
    page.render()
    button = next(button for button in page.action_buttons if button.text() == 'Stáhnout')
    assert button.isEnabled()
    button.click()
    qtbot.waitUntil(lambda: not operations.active and not page.busy, timeout=30000)
    record = next(iter(operations.records.values()))
    assert record.result, record.error.detail
    assert record.result['status'] == expected
    assert record.terminal == expected
    assert 'bezpečně ověřeny' not in page.notice.text(), 'Pending/partial result was announced as verified'
    word = 'neúplné' if expected == 'partial' else 'zpracovává' if expected == 'batch_pending' else 'funkčnost'
    assert word in page.notice.text().lower()
    if expected == 'files_complete_unverified':
        assert (tmp_path/'OUT/src/hello.txt').read_bytes() == 'Přesný výsledek\n'.encode()
    else:
        assert not list((tmp_path/'OUT').rglob('*'))
    assert calls == ([('GET', '/batches/batch_notice'), ('GET', '/files/file_bad/content')]
                     if expected != 'batch_pending' else [('GET', '/batches/batch_notice')])
    for item in operations.records.values():
        if item.dialog: item.dialog.close()
