"""SMTP a SSH diagnostika přes nativní orchestrace; transporty jsou izolované."""

import base64
import hashlib
import io
import json

from native_provider_fixtures import native_fixture
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QPushButton

from test_settings_http_closure import fixture
from test_qa_qfile_http_closure import WorkflowHttp, page_fixture, settle


class MailTransport:
    def __init__(self, fault='', barrier=False):
        self.fault, self.barrier = fault, barrier
        self.calls, self.messages = [], []
        self.entered, self.release = threading.Event(), threading.Event()

    def construct(self, **kwargs):
        self.calls.append(('connect', kwargs))
        if self.fault == 'connect':
            raise TimeoutError('syntetický timeout SMTP')
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.calls.append(('close', None))

    def ehlo(self):
        self.calls.append(('ehlo', None))
        return 250, b'OK'

    def starttls(self, **kwargs):
        self.calls.append(('tls', bool(kwargs['context'])))
        return 220, b'TLS ready'

    def login(self, name, password):
        self.calls.append(('login', name))
        assert password == 'synthetic-password'
        if self.fault == 'auth':
            raise OSError('syntetická chyba autentizace')
        return 235, b'Authenticated'

    def send_message(self, message):
        self.messages.append(message.as_bytes())
        self.calls.append(('send', None))
        if self.barrier:
            self.entered.set()
            assert self.release.wait(20), 'SMTP bariéra nebyla uvolněna'
        if self.fault == 'send':
            raise ConnectionResetError('syntetické přerušení SMTP')
        return {}


@pytest.mark.parametrize('security,fault', [('tls', ''), ('ssl', ''), ('plain', ''), ('tls', 'connect'), ('tls', 'auth'), ('tls', 'send')])
def test_smtp_settings_action_real_worker_and_transport(qtbot, monkeypatch, tmp_path, security, fault):
    page, context, _, _ = fixture(qtbot, monkeypatch, tmp_path)
    transport = MailTransport(fault)
    monkeypatch.setattr('kajovo.core.notifications.smtplib.SMTP', transport.construct)
    monkeypatch.setattr('kajovo.core.notifications.smtplib.SMTP_SSL', transport.construct)
    for key, value in {'smtp.host':'offline.invalid', 'smtp.username':'synthetic-user', 'smtp.password':'synthetic-password', 'smtp.to_email':'test@example.invalid'}.items():
        page.editors[key].setText(value)
    page.editors['smtp.use_tls'].setChecked(security == 'tls')
    page.editors['smtp.use_ssl'].setChecked(security == 'ssl')
    page.findChild(QPushButton, 'settings.smtp.test').click()
    qtbot.waitUntil(lambda: not context.operations.active, timeout=30000)
    record = list(context.operations.records.values())[-1]
    qtbot.addWidget(record.dialog)
    record.dialog.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert record.terminal == ('failed' if fault else 'completed')
    if fault:
        assert record.error and 'Zkušební zpráva byla odeslána' not in page.notice.text()
    else:
        assert page.notice.text() == 'Zkušební zpráva byla odeslána.'
        assert len(transport.messages) == 1
        assert b'To: test@example.invalid' in transport.messages[0]
        assert ('tls', True) in transport.calls if security == 'tls' else not any(c[0] == 'tls' for c in transport.calls)
    assert not any('synthetic-password' in p.read_text(errors='replace', encoding="utf-8") for p in tmp_path.rglob('*.json'))


def test_smtp_old_result_does_not_confirm_changed_configuration(qtbot, monkeypatch, tmp_path):
    page, context, _, _ = fixture(qtbot, monkeypatch, tmp_path)
    transport = MailTransport(barrier=True)
    monkeypatch.setattr('kajovo.core.notifications.smtplib.SMTP', transport.construct)
    page.editors['smtp.host'].setText('old.invalid')
    page.editors['smtp.to_email'].setText('test@example.invalid')
    page.findChild(QPushButton, 'settings.smtp.test').click()
    qtbot.waitUntil(transport.entered.is_set)
    page.editors['smtp.host'].setText('new.invalid')
    page.notice.setText('Nové nastavení čeká na ověření.')
    transport.release.set()
    qtbot.waitUntil(lambda: not context.operations.active)
    record = list(context.operations.records.values())[-1]
    qtbot.addWidget(record.dialog)
    record.dialog.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert record.terminal == 'completed'
    assert page.notice.text() == 'Nové nastavení čeká na ověření.'
    assert transport.calls[0][1]['host'] == 'old.invalid'


class SshTransport:
    key = b'synthetic-host-key'

    def __init__(self, fault):
        self.fault, self.calls, self.closed = fault, [], False

    def load_system_host_keys(self):
        self.calls.append('host_keys')

    def set_missing_host_key_policy(self, policy):
        assert type(policy).__name__ == 'RejectPolicy'

    def connect(self, **kwargs):
        self.calls.append(('connect', kwargs))
        assert kwargs['hostname'] == 'offline.invalid'
        if self.fault == 'connect':
            raise TimeoutError('syntetický SSH timeout')

    def get_transport(self):
        return SimpleNamespace(get_remote_server_key=lambda: SimpleNamespace(asbytes=lambda: self.key))

    def exec_command(self, command, **kwargs):
        self.calls.append(('command', command))
        if self.fault == 'command':
            raise ConnectionResetError('syntetické SSH přerušení')
        return io.BytesIO(), io.BytesIO(('DOLOŽENO ' + command + '\n').encode()), io.BytesIO()

    def close(self):
        self.closed = True


class DiagnosticsHttp(WorkflowHttp):
    def request(self, method, url, **kwargs):
        path = url.split('/v1', 1)[1]
        if path == '/files' and method == 'POST':
            assert kwargs['data'] == {'purpose':'user_data'}
            binary = kwargs['files']['file'][1].read()
            payload = json.loads(binary)
            assert payload['file_count'] == 1
            assert 'DOLOŽENO uname -a' in payload['files'][0]['content']
            self.calls.append({'method':method, 'path':path, 'multipart_bytes':list(binary)})
            import requests
            response = requests.Response()
            response.status_code = 200
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps(native_fixture(method, path, {"id": "file_diag", "filename": "diagnostics.json", "bytes": len(binary), "purpose": "user_data"}, kwargs["data"])).encode()
            return response
        if path == '/files/file_diag':
            import requests
            response = requests.Response()
            response.status_code = 200
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps(native_fixture(method, path, {"id": "file_diag", "filename": "diagnostics.json", "bytes": 128, "purpose": "user_data"})).encode()
            return response
        if path.startswith('/vector_stores'):
            import requests
            if path == '/vector_stores' and method == 'POST':
                assert kwargs['json']['name'].startswith('DIAG_')
                value = {'id':'vs_diag','status':'completed','file_counts':{'completed':1,'in_progress':0,'failed':0,'cancelled':0,'total':1}}
            elif path == '/vector_stores/vs_diag/files' and method == 'POST':
                assert kwargs['json'] == {'file_id':'file_diag'}
                value = {'id':'file_diag','vector_store_id':'vs_diag','status':'completed'}
            elif path == '/vector_stores/vs_diag/files/file_diag':
                value = {'id':'file_diag','vector_store_id':'vs_diag','status':'completed'}
            elif path == '/vector_stores/vs_diag':
                value = {'id':'vs_diag','status':'completed','file_counts':{'completed':1,'in_progress':0,'failed':0,'cancelled':0,'total':1}}
            else:
                raise AssertionError((method,path))
            self.calls.append({'method':method,'path':path,'body':kwargs.get('json')})
            response = requests.Response()
            response.status_code = 200
            response.headers['Content-Type'] = 'application/json'
            response._content = json.dumps(native_fixture(method, path, value, kwargs.get("json") or kwargs.get("data"))).encode()
            return response
        return super().request(method, url, **kwargs)


@pytest.mark.parametrize('fault', ['', 'connect', 'pin', 'command'])
def test_qa_ssh_diagnostics_ui_transport_archive_and_request(qtbot, monkeypatch, tmp_path, fault):
    transport = DiagnosticsHttp(tmp_path)
    ssh = SshTransport(fault)
    monkeypatch.setattr('kajovo.core.diagnostics.ssh.paramiko.SSHClient', lambda: ssh)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, 'QA', transport)
    page.widgets['diag_ssh_in'].setChecked(True)
    page.widgets['ssh_host'].setText('offline.invalid')
    page.widgets['ssh_user'].setText('synthetic-user')
    pin = base64.b64encode(hashlib.sha256(ssh.key).digest()).decode().rstrip('=')
    page.widgets['ssh_pin'].setText('SHA256:' + ('wrong' if fault == 'pin' else pin))
    page.widgets['ssh_pin_required'].setChecked(True)
    page.start_button.click()
    settle(qtbot, operations, page)
    record = list(operations.records.values())[-1]
    assert ssh.closed
    if fault:
        assert record.terminal == 'failed' and record.error
        assert not any(c['path'] == '/responses' for c in transport.calls)
        if fault == 'pin':
            assert not any(isinstance(c, tuple) and c[0] == 'command' for c in ssh.calls)
    else:
        assert record.terminal == 'completed', record.error
        state = json.loads((tmp_path / 'LOG' / record.identifier / 'run_state.json').read_text(encoding="utf-8"))
        assert state['diagnostics_delivery'] == {'requested':True, 'delivered':True, 'file_ids':['file_diag']}
        request = next(c['body'] for c in transport.calls if c['path'] == '/responses')
        assert 'file_diag' in json.dumps(request['input'])
        assert [c[1] for c in ssh.calls if isinstance(c, tuple) and c[0] == 'command'] == ['uname -a','whoami','uptime']
