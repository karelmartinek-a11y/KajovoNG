"""Neúplná potvrzení mutací a částečné mazání přes skutečné Resources UI."""
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from test_resources_http_closure import ResourcesHttp, ValueDialog, click, finish, page_fixture
from test_runtime_end_to_end import child


class AckHttp(ResourcesHttp):
    def __init__(self, root, fault):
        super().__init__(root)
        self.fault = fault

    def request(self, method, url, **kwargs):
        path = url.split('/v1', 1)[1]
        if method == 'DELETE' and self.fault == 'delete':
            self.calls.append({'method': method, 'path': path})
            import requests
            response = requests.Response()
            response.status_code = 200
            response.headers['Content-Type'] = 'application/json'
            response._content = json.dumps({'id': 'foreign', 'deleted': False}).encode()
            return response
        response = super().request(method, url, **kwargs)
        if method == 'POST' and self.fault in {'files', 'stores', 'member'}:
            if path in {'/files', '/vector_stores', '/vector_stores/vs_one/files'}:
                response._content = json.dumps({'id': None} if self.fault != 'member' else {'id':'file_1', 'vector_store_id':'vs_foreign', 'status':'completed'}).encode()
        return response


@pytest.mark.parametrize('kind', ['files', 'stores'])
def test_missing_create_identity_is_unknown_then_restart_only_reads(qtbot, monkeypatch, tmp_path, kind):
    transport = AckHttp(tmp_path, kind)
    page, _ = page_fixture(qtbot, tmp_path, transport)
    source = tmp_path / 'zdroj.txt'
    source.write_bytes(b'Original bytes\n')
    monkeypatch.setattr('kajovo.studio.resources.get_open_file_names', lambda *a: ([str(source)], ''))
    def accept(dialog):
        dialog.value = 'Testovací úložiště'
        return QDialog.Accepted
    monkeypatch.setattr(ValueDialog, 'exec', accept)
    click(page, f'resources.{kind}.create')
    assert finish(qtbot, page).terminal == 'submission_unknown'
    child(f"from pathlib import Path; import sys; from test_resource_ack_http_closure import restart_read; restart_read(Path(sys.argv[1]), {kind!r})", tmp_path)
    assert sum(r['method'] == 'POST' for r in transport.calls) == 1


def restart_read(root, kind):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    transport = ResourcesHttp(root)
    from test_resources_http_closure import client_for
    client = client_for(transport)
    rows = client.list_files() if kind == 'files' else client.list_vector_stores()
    assert len(rows) == 1 and rows[0]['id']
    assert all(row['method'] == 'GET' for row in transport.calls)
    app.processEvents()


@pytest.mark.parametrize('kind', ['files', 'stores'])
def test_delete_requires_matching_identity_and_deleted_true(qtbot, monkeypatch, tmp_path, kind):
    transport = AckHttp(tmp_path, 'delete')
    transport.state['files']['file_1'] = {'id':'file_1', 'filename':'zdroj.txt', 'content':'original'}
    transport.state['stores']['vs_one'] = {'id':'vs_one', 'name':'Testovací úložiště'}
    page, context = page_fixture(qtbot, tmp_path, transport)
    click(page, f'resources.{kind}.refresh')
    finish(qtbot, page)
    context.files, context.stores = ['file_1'], ['vs_one']
    page.lists[kind].item(0).setSelected(True)
    monkeypatch.setattr('kajovo.studio.resources.confirm', lambda *a: True)
    click(page, f'resources.{kind}.delete')
    assert finish(qtbot, page).terminal == 'submission_unknown'
    assert page.lists[kind].count() == 1
    assert context.files == ['file_1'] and context.stores == ['vs_one']
    assert sum(r['method'] == 'DELETE' for r in transport.calls) == 1


def test_member_ack_cannot_belong_to_other_store(qtbot, tmp_path):
    transport = AckHttp(tmp_path, 'member')
    transport.state['files']['file_1'] = {'id':'file_1', 'filename':'zdroj.txt', 'content':'original'}
    transport.state['stores']['vs_one'] = {'id':'vs_one', 'name':'Testovací úložiště', 'file_counts':{'completed':0, 'failed':0, 'cancelled':0, 'in_progress':0, 'total':0}}
    page, _ = page_fixture(qtbot, tmp_path, transport)
    for kind in ['files', 'stores']:
        click(page, f'resources.{kind}.refresh')
        finish(qtbot, page)
    page.lists['files'].item(0).setSelected(True)
    page.lists['stores'].setCurrentRow(0)
    click(page, 'resources.store.add_selected')
    assert finish(qtbot, page).terminal == 'submission_unknown'
    assert sum(r['method'] == 'POST' for r in transport.calls) == 1


def test_partial_delete_prunes_only_confirmed_file(qtbot, monkeypatch, tmp_path):
    transport = ResourcesHttp(tmp_path)
    for identifier in ['file_1', 'file_2']:
        transport.state['files'][identifier] = {'id':identifier, 'filename':identifier+'.txt', 'content':identifier}
    transport.fail = 'file_2'
    page, context = page_fixture(qtbot, tmp_path, transport)
    click(page, 'resources.files.refresh')
    finish(qtbot, page)
    context.files = ['file_1', 'file_2']
    for index in range(2):
        page.lists['files'].item(index).setSelected(True)
    monkeypatch.setattr('kajovo.studio.resources.confirm', lambda *a: True)
    click(page, 'resources.files.delete')
    assert finish(qtbot, page).terminal == 'failed'
    assert context.files == ['file_2']
    assert page.lists['files'].count() == 1
    assert page.lists['files'].item(0).data(Qt.UserRole) == 'file_2'
    assert list(transport.state['files']) == ['file_2']
