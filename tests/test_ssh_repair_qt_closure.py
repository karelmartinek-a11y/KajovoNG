"""Potvrzená SSH oprava přes dialog, QThread a izolovaný transport."""
import base64
import hashlib
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from kajovo.studio.components import DetailDialog
from test_qa_qfile_http_closure import WorkflowHttp, page_fixture, settle


class RepairSsh:
    def __init__(self, block=False, code=0):
        self.block, self.code = block, code
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls, self.written = [], []
        self.closed = False
        self.channel = SimpleNamespace(shutdown_write=lambda: self.calls.append('shutdown'), recv_exit_status=lambda: code)

    def load_system_host_keys(self):
        self.calls.append('known_hosts')

    def set_missing_host_key_policy(self, policy):
        self.calls.append(type(policy).__name__)

    def connect(self, **kwargs):
        self.calls.append(kwargs)

    def get_transport(self):
        return SimpleNamespace(get_remote_server_key=lambda: SimpleNamespace(asbytes=lambda: b'synthetic host key'))

    def exec_command(self, command, timeout):
        self.calls.append((command, timeout))
        return self, self, self

    def write(self, content):
        self.written.append(content)

    def flush(self):
        self.calls.append('flush')

    def read(self):
        self.entered.set()
        if self.block:
            assert self.release.wait(20), 'Chybí uvolnění SSH bariéry'
        return 'Přesný výsledek opravného procesu\n'.encode()

    def close(self):
        self.closed = True


@pytest.mark.parametrize('case', ['accepted', 'rejected', 'failed', 'changed_script', 'stale'])
def test_confirmed_ssh_repair_actual_dialog_worker_bytes_and_context(qtbot, monkeypatch, tmp_path, case):
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, 'QA', WorkflowHttp(tmp_path))
    root = tmp_path / 'OUT'
    root.mkdir()
    script = root / 'run_this_script_repairme_kajovo.sh'
    script.write_bytes(b'echo synthetic approved\n')
    (root / 'readmerepair.txt').write_text('Popis potvrzené opravy\n', encoding="utf-8")
    cfg = page.config()
    cfg.ssh_host, cfg.ssh_user = 'offline.invalid', 'synthetic'
    cfg.ssh_pin = 'SHA256:' + base64.b64encode(hashlib.sha256(b'synthetic host key').digest()).decode().rstrip('=')
    cfg.ssh_pin_required = True
    transport = RepairSsh(case == 'stale', 1 if case == 'failed' else 0)
    constructions = []
    def connect():
        constructions.append(True)
        return transport
    monkeypatch.setattr('kajovo.core.diagnostics.ssh.paramiko.SSHClient', connect)
    timer = QTimer(page)
    confirmations = []
    def confirm():
        dialogs = page.findChildren(DetailDialog)
        if not dialogs:
            return
        confirmations.append(True)
        if case == 'changed_script':
            script.write_bytes(b'echo changed after preview\n')
        dialogs[-1].findChild(QPushButton, 'dialog.close' if case == 'rejected' else 'dialog.confirm').click()
        timer.stop()
    timer.timeout.connect(confirm)
    timer.start(10)
    try:
        page.offer_repair(cfg, True)
    finally:
        timer.stop()
    assert len(confirmations) == 1
    if case == 'rejected':
        assert not operations.records and not constructions
        assert not (root / '_repair_ssh_exec_log.txt').exists()
        return
    if case == 'stale':
        qtbot.waitUntil(transport.entered.is_set)
        page.apply_state({'prompt': 'Nový kontext'})
        page.result.set_value({'status': 'waiting', 'text': 'Výsledek nového kontextu'})
        transport.release.set()
    settle(qtbot, operations, page)
    record = next(iter(operations.records.values()))
    assert record.terminal == ('failed' if case in {'failed', 'changed_script'} else 'completed')
    if case == 'changed_script':
        assert not constructions
        return
    assert transport.written == [b'echo synthetic approved\n']
    assert ('sh -s 2>&1', 120) in transport.calls and 'RejectPolicy' in transport.calls
    assert transport.closed
    log = (root / '_repair_ssh_exec_log.txt').read_text(encoding="utf-8")
    assert 'Přesný výsledek opravného procesu\n' in log and 'Popis potvrzené opravy' in log
    if case == 'stale':
        assert page.result.value == {'status': 'waiting', 'text': 'Výsledek nového kontextu'}
    elif case == 'accepted':
        assert page.result.value['status'] == 'completed'
