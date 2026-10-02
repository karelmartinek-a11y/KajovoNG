"""Smíšená výroba přes Qt, skutečné TCP a dva nezávislé procesy."""
import base64
import copy
from native_provider_fixtures import native_fixture
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
import pytest

from change_v2_fixtures import V2Responder
from test_delivery_http_graph import RecordingHttp, expected_content, graph_files

ROOT = Path(__file__).resolve().parents[1]


def source_id(file_id):
    return 'SRC-REMOTE-' + hashlib.sha256(file_id.encode()).hexdigest()[:20]


def png(color, size=16):
    stream = io.BytesIO()
    Image.new('RGB', (size, size), color).save(stream, format='PNG')
    return stream.getvalue()


def document(text):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('[Content_Types].xml', (2026, 1, 1, 0, 0, 0)), '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr(zipfile.ZipInfo('_rels/.rels', (2026, 1, 1, 0, 0, 0)), '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr(zipfile.ZipInfo('word/document.xml', (2026, 1, 1, 0, 0, 0)), '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>')
    return stream.getvalue()


SOURCES = {'file_blue': ('logo.png', png('blue')), 'file_red': ('logo.png', png('red')),
           'file_doc': ('original.docx', document('Schválený dokument'))}
GENERATED = png('green', 1024)
MANUAL = document('Ruční dokument')


def mixed_files(mode):
    rows = graph_files(mode)
    rows.extend({'path': name, 'action': 'generate' if mode == 'GENERATE' else 'add',
                 'kind': 'binary_required'} for name in
                ['assets/generated.png', 'assets/rendered.png', 'assets/existing.docx', 'z_manual.docx'])
    return rows


def deliveries():
    return [{'path': path, 'producer': producer, 'source_or_task_id': identity,
             'criterion_ids': ['AC-1'], **extra}
            for path, producer, identity, extra in [
                ('assets/generated.png', 'image_workflow', 'IMG-1',
                 {'image_production': {'version': 1, 'size': '1024x1024', 'background': 'opaque'}}),
                ('assets/rendered.png', 'local_renderer', source_id('file_red'), {}),
                ('assets/existing.docx', 'existing_asset', source_id('file_doc'), {}),
                ('z_manual.docx', 'manual_input', 'MANUAL-DOC', {})]]


class MixedHttp(RecordingHttp):
    def __init__(self, root, mode):
        super().__init__(root, mode)
        def enrich(name, value, data):
            if name in {'A2_SPINE_V2', 'B2_SPINE_V2'}:
                value['result']['data']['resource_deliveries'] = deliveries()
            return value
        self.responder = V2Responder(mode, files=mixed_files(mode), mutate=enrich)

    def request(self, method, url, **kwargs):
        import requests
        path = url.split('/v1', 1)[1].split('?', 1)[0]
        identifier = path.split('/')[2] if path.startswith('/files/') else ''
        if identifier in SOURCES or path == '/images/generations' or (method == 'GET' and path == '/batches'):
            self.calls.append({'method': method, 'path': path, 'body': copy.deepcopy(kwargs.get('json'))})
            raw = None
            if identifier in SOURCES:
                name, binary = SOURCES[identifier]
                if path.endswith('/content'):
                    raw = binary
                    value = None
                else:
                    value = {'id': identifier, 'filename': name, 'bytes': len(binary), 'purpose': 'user_data'}
            elif path == '/images/generations':
                assert method == 'POST'
                body = kwargs['json']
                assert body['model'] == 'gpt-image-2.5-sunburst-2026-09-08'
                assert body['size'] == '1024x1024' and body['background'] == 'opaque'
                value = {'created': 1790870400, 'data': [{'b64_json': base64.b64encode(GENERATED).decode()}]}
            else:
                value = {'data': [{**row, 'status': 'completed', 'output_file_id': 'file_output_' + bid,
                                   'error_file_id': None, 'request_counts': {'total': 1, 'completed': 1, 'failed': 0}}
                                  for bid, row in self.state['batches'].items()], 'has_more': False}
            result = requests.Response()
            result.status_code = 200
            result.headers['Content-Type'] = 'application/octet-stream' if raw else 'application/json'
            result._content = raw if raw else json.dumps(native_fixture(method, path, value, kwargs.get("json") or kwargs.get("data"))).encode()
            return result
        return super().request(method, url, **kwargs)


def run_phase(root, url, mode, batch, phase):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QInputDialog
    from kajovo.core.config import AppSettings
    from kajovo.core.openai_client import OpenAIClient
    from kajovo.core.run_bundle import LegacyRunAdapter, RunBundle
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.workbench import Workbench
    from kajovo.studio.history import HistoryPage
    from kajovo.studio.history_composer import BranchComposer
    from kajovo.studio.history_models import build_run
    import kajovo.core.batch_completion as implementation
    print(json.dumps({'module': implementation.__file__, 'source_sha256': hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest()}), flush=True)
    app = QApplication([])
    def factory(*args, **kwargs):
        client = OpenAIClient('synthetic', base_url=url)
        client._sdk = None
        return client
    manager = Operations(None)
    context = StudioContext(AppSettings(log_dir=str(root/'LOG'), cache_dir=str(root/'cache')),
                            manager, api_key='synthetic', client_factory=factory)
    context.models = ['gpt-4o-mini', 'gpt-image-1.5']
    page = Workbench(context)
    manager.setParent(page)
    history = HistoryPage(context, page, page)
    def wait(predicate):
        loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start(10)
        deadline.start(30000)
        loop.exec()
        poll.stop()
        assert predicate(), [(r.terminal, str(r.error)) for r in manager.records.values()]
    def select(run):
        adapter = LegacyRunAdapter(root/'LOG'/run)
        history.load_run(build_run(adapter.run_record(), steps=adapter.steps()))
        wait(lambda: history.adapter is not None and not manager.active)
        return adapter
    with patch('kajovo.core.runs.executor.OpenAIClient', factory):
        if phase == 'prepare':
            source = root/'IN'
            source.mkdir()
            if mode == 'MODIFY':
                for name in ['seed.txt', 'preserved.txt']:
                    (source/name).write_bytes(b'original\n')
            page.apply_state({'project': 'Smíšené resources '+mode, 'prompt': 'Tři obsahové vlny, obrazy a dokumenty.',
                              'mode': mode, 'model': 'gpt-4o-mini', 'model_a1': 'gpt-4o-mini',
                              'model_a2': 'gpt-4o-mini', 'model_a3': 'gpt-4o-mini',
                              'attached_file_ids': list(SOURCES), 'in_dir': str(source) if mode == 'MODIFY' else '',
                              'out_dir': str(root/'OUT'), 'maximum_quality': True, 'send_as_c': batch})
            page.start_button.click()
            wait(lambda: not manager.active and any(r.identifier.startswith('RUN_') for r in manager.records.values()))
            record = next(r for r in manager.records.values() if r.identifier.startswith('RUN_'))
            assert record.result, str(record.error)
            run = record.identifier
            assert record.result['status'] == ('batch_pending' if batch else 'waiting_manual_resource')
            adapter = select(run)
            if batch:
                for wave in range(3):
                    # Read-only polling stores the confirmed remote identity through the actual panel.
                    from kajovo.studio.batches import BatchesPage
                    panel = BatchesPage(context)
                    panel.refresh_button.click()
                    wait(lambda panel=panel: not manager.active and not panel.busy)
                    adapter = select(run)
                    assert history.buttons['complete_batch'].isEnabled(), history._state
                    history.buttons['complete_batch'].click()
                    wait(lambda: not manager.active)
                    adapter = select(run)
                    assert history._state['status'] == ('batch_pending' if wave < 2 else 'waiting_manual_resource')
            assert not list((root/'OUT').rglob('*')) if (root/'OUT').exists() else True
            state = json.loads((adapter.root/'run_state.json').read_text())
            assert len(state['staged_files']) == 6
            assert state['source_pack_id'] == state['source_pack']['pack_id']
            from kajovo.core.orchestration.contracts import canonical_sha256
            assert canonical_sha256(state['source_pack']) == state['source_pack_hash']
            for identifier, (_, binary) in SOURCES.items():
                source_row = next(x for x in state['source_pack']['sources'] if x['id'] == source_id(identifier))
                assert source_row['sha256'] == hashlib.sha256(binary).hexdigest()
                assert source_row['byte_length'] == len(binary)
            for name in ['seed.txt', 'middle.txt', 'final.txt']:
                row = next(x for x in state['staged_files'] if x['path'] == name)
                assert (adapter.root/row['staged_path']).read_bytes() == expected_content(mode, name).encode()
            assert adapter.bundle.verify_control_bindings() == []
            for row in state['staged_files']:
                assert hashlib.sha256((adapter.root/row['staged_path']).read_bytes()).hexdigest() == row['sha256']
            (root/'phase.json').write_text(json.dumps({'run': run, 'pid': os.getpid(), 'status': state['status']}))
        else:
            before = json.loads((root/'phase.json').read_text())
            assert before['pid'] != os.getpid()
            run = before['run']
            adapter = select(run)
            assert history._state['status'] == 'waiting_manual_resource'
            manual = root/'manual.docx'
            manual.write_bytes(MANUAL)
            with patch.object(QInputDialog, 'getItem', return_value=('z_manual.docx', True)), patch('kajovo.studio.history.get_open_file_name', return_value=(str(manual), '')):
                history.buttons['manual_resource'].click()
                wait(lambda: not manager.active)
            adapter = select(run)
            if batch:
                picker_timer = QTimer()
                def choose_existing_batch():
                    dialogs = [dialog for dialog in history.findChildren(QInputDialog) if dialog.isVisible()]
                    if dialogs:
                        selected = next(label for label in dialogs[-1].comboBoxItems()
                                        if label.endswith(history._state['batch_id']))
                        dialogs[-1].setTextValue(selected)
                        dialogs[-1].accept()
                        picker_timer.stop()
                picker_timer.timeout.connect(choose_existing_batch)
                picker_timer.start(10)
                history.buttons['complete_batch'].click()
                picker_timer.stop()
                wait(lambda: not manager.active)
                adapter = select(run)
            else:
                parent_bytes = {str(p.relative_to(adapter.root)): p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
                parent_root = adapter.root
                confirmed = []
                timer = QTimer()
                def confirm():
                    dialogs = history.findChildren(BranchComposer)
                    if dialogs and dialogs[-1].preview and dialogs[-1].confirm_button.isEnabled():
                        confirmed.append(dialogs[-1].preview)
                        dialogs[-1].confirm_button.click()
                        timer.stop()
                timer.timeout.connect(confirm)
                timer.start(10)
                assert history.buttons['continue'].isEnabled()
                history.buttons['continue'].click()
                wait(lambda: not manager.active)
                assert len(confirmed) == 1
                child = next(r for r in manager.records.values() if r.identifier.startswith('RUN_') and r.identifier != run)
                assert child.result, str(child.error)
                run = child.identifier
                assert parent_bytes == {str(p.relative_to(parent_root)): p.read_bytes() for p in parent_root.rglob('*') if p.is_file()}
                adapter = select(run)
            assert history._state['status'] == 'files_complete_unverified', history._state
            history.buttons['publish_staged'].click()
            wait(lambda: not manager.active)
            adapter = select(run)
            assert history._state['status'] == 'completed_unverified', [(r.terminal, str(r.error)) for r in manager.records.values()]
            assert not history.buttons['publish_staged'].isEnabled()
            expected = {'assets/generated.png': GENERATED, 'assets/rendered.png': SOURCES['file_red'][1],
                        'assets/existing.docx': SOURCES['file_doc'][1], 'z_manual.docx': MANUAL}
            expected.update({p: expected_content(mode, p).encode() for p in ['seed.txt', 'middle.txt', 'final.txt']})
            for name, binary in expected.items():
                actual = (root/'OUT'/name).read_bytes()
                assert actual == binary, name
                if name.endswith('.png'):
                    with Image.open(io.BytesIO(actual)) as image:
                        image.load()
                        assert image.format == 'PNG'
                if name.endswith('.docx'):
                    with zipfile.ZipFile(io.BytesIO(actual)) as archive:
                        assert archive.testzip() is None
                        text = ''.join(ET.fromstring(archive.read('word/document.xml')).itertext())
                        assert text == ('Ruční dokument' if name == 'z_manual.docx' else 'Schválený dokument')
            if mode == 'MODIFY':
                assert (root/'IN/preserved.txt').read_bytes() == b'original\n'
                assert (root/'IN/seed.txt').read_bytes() == b'original\n'
            assert RunBundle(adapter.root).verify_integrity()['valid'], RunBundle(adapter.root).verify_integrity()
            sealed = {str(p.relative_to(adapter.root)): p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
            from kajovo.core.orchestration.publish import publish_staged_run
            assert publish_staged_run(str(adapter.root))['status'] == 'committed'
            assert sealed == {str(p.relative_to(adapter.root)): p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
            (root/'result.json').write_text(json.dumps({'run': run, 'pid': os.getpid(), 'status': history._state['status'],
                                                       'hashes': {p: hashlib.sha256(v).hexdigest() for p, v in expected.items()}}))
    for record in manager.records.values():
        if record.dialog:
            record.dialog.close()
    page.close()
    app.processEvents()


@pytest.mark.parametrize('mode', ['GENERATE', 'MODIFY'])
@pytest.mark.parametrize('batch', [False, True])
def test_mixed_resources_three_waves_restart_and_history_publish(tmp_path, mode, batch):
    transport = MixedHttp(tmp_path, mode)
    accepted = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            kwargs = {'headers': {}}
            if raw and self.headers.get('Content-Type', '').startswith('multipart/'):
                msg = BytesParser(policy=default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\n\r\n').encode()+raw)
                kwargs.update(files={}, data={})
                for part in msg.iter_parts():
                    name = part.get_param('name', header='content-disposition')
                    value = part.get_payload(decode=True)
                    if part.get_filename():
                        kwargs['files'][name] = (part.get_filename(), io.BytesIO(value))
                    else:
                        kwargs['data'][name] = value.decode()
            elif raw:
                kwargs['json'] = json.loads(raw)
            row = {'method': self.command, 'path': self.path, 'body': kwargs.get('json'),
                   'raw_sha256': hashlib.sha256(raw).hexdigest()}
            accepted.append(row)
            try:
                response = transport.request(self.command, 'https://offline.invalid'+self.path, **kwargs)
                body, status = response.content, response.status_code
            except Exception as exc:
                row['error'] = repr(exc)
                (tmp_path/'http-errors.json').write_text(json.dumps(accepted, default=str))
                body, status = json.dumps({'error': {'message': repr(exc)}}).encode(), 500
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT/'tests')]),
               NO_PROXY='127.0.0.1,localhost', OPENAI_API_KEY='', PYTHON_KEYRING_BACKEND='keyring.backends.null.Keyring')
    try:
        for phase in ['prepare', 'resume']:
            posts_before = sum(r['method'] == 'POST' for r in accepted)
            code = 'from pathlib import Path; import sys; from test_mixed_resource_http_closure import run_phase; run_phase(Path(sys.argv[1]),sys.argv[2],sys.argv[3],sys.argv[4]=="True",sys.argv[5])'
            child = subprocess.run([sys.executable, '-c', code, str(tmp_path), f'http://127.0.0.1:{server.server_port}/v1', mode, str(batch), phase],
                                   cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
            (tmp_path/(phase+'-stdout.txt')).write_text(child.stdout+child.stderr)
            assert child.returncode == 0, child.stdout+child.stderr
            if phase == 'resume':
                assert sum(r['method'] == 'POST' for r in accepted) == posts_before
        assert sum(r['path'] == '/v1/images/generations' for r in accepted) == 1
        assert sum(r['method'] == 'POST' and r['path'] == '/v1/batches' for r in accepted) == (3 if batch else 0)
        assert json.loads((tmp_path/'result.json').read_text())['status'] == 'completed_unverified'
        (tmp_path/'transport-calls.json').write_text(json.dumps(transport.calls, ensure_ascii=False, indent=2))
        (tmp_path/'accepted.json').write_text(json.dumps(accepted, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
