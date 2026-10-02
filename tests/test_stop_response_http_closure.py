"""Stop na přijatém HTTP požadavku zachová odpověď, ale nepokračuje ve výrobě."""
import json
from native_provider_fixtures import native_fixture
import threading
import hashlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

LOCAL_REQUEST = requests.sessions.Session.request

from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from kajovo.studio.workbench import Workbench
from test_qa_qfile_http_closure import qa_value, plan_value


@pytest.mark.parametrize('mode', ['QA', 'QFILE'])
def test_stop_after_http_acceptance_preserves_id_and_blocks_success(qtbot, monkeypatch, tmp_path, mode):
    import kajovo.core.runs.response_execution as implementation
    (tmp_path/'provenance.json').write_text(json.dumps({'module': implementation.__file__, 'source_sha256': hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest()}))
    entered, release = threading.Event(), threading.Event()
    accepted = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            assert self.path == '/v1/models'
            raw = json.dumps(native_fixture('GET', '/models', {'data': [{'id': 'gpt-4o-mini'}], 'has_more': False})).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            accepted.append({'path': self.path, 'body': body})
            if self.path == '/v1/responses/input_tokens':
                value = {'input_tokens': 128}
            else:
                assert self.path == '/v1/responses'
                entered.set()
                assert release.wait(20)
                answer = qa_value() if mode == 'QA' else plan_value()
                value = {'id': 'resp_barrier', 'status': 'completed', 'model': 'gpt-4o-mini',
                         'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(answer)}]}],
                         'usage': {'input_tokens': 128, 'output_tokens': 32}}
            raw = json.dumps(native_fixture("POST", self.path.removeprefix("/v1"), value, body)).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def factory(*args, **kwargs):
        client = OpenAIClient('synthetic-offline', base_url=f'http://127.0.0.1:{server.server_port}/v1')
        client._sdk = None
        client.session.trust_env = False
        def local_request(method, url, **options):
            assert url.startswith(f'http://127.0.0.1:{server.server_port}/v1/')
            return LOCAL_REQUEST(client.session, method, url, **options)
        client.session.request = local_request
        return client
    operations = Operations(None)
    context = StudioContext(AppSettings(log_dir=str(tmp_path/'LOG'), cache_dir=str(tmp_path/'cache')),
                            operations, api_key='synthetic-offline', client_factory=factory)
    context.models = ['gpt-4o-mini']
    page = Workbench(context)
    operations.setParent(page)
    qtbot.addWidget(page)
    monkeypatch.setattr('kajovo.core.runs.executor.OpenAIClient', factory)
    page.apply_state({'project': 'Stop při odpovědi', 'prompt': 'Doložené zadání.', 'mode': mode,
                      'model': 'gpt-4o-mini', 'out_dir': str(tmp_path/'OUT'),
                      'qfile_suggest_path': mode == 'QFILE', 'qfile_output_format': 'md'})
    try:
        page.start_button.click()
        qtbot.waitUntil(entered.is_set, timeout=10000)
        record = next(iter(operations.records.values()))
        record.dialog.stop.click()
        release.set()
        qtbot.waitUntil(lambda: not operations.active and not page.busy_outputs, timeout=10000)
        assert record.terminal == 'cancelled', 'Stop před odpovědí nesmí skončit jako úspěch'
        assert not record.result and not record.result_received
        assert sum(r['path'] == '/v1/responses' for r in accepted) == 1
        root = tmp_path/'LOG'/record.identifier
        state = json.loads((root/'run_state.json').read_text())
        assert state['status'] == 'stopped'
        raw = list((root/'responses').glob('*received_resp_barrier_*.json'))
        assert len(raw) == 1 and json.loads(raw[0].read_text())['id'] == 'resp_barrier'
        assert not list(root.glob('manifests/*QA_answer*.json'))
        assert not list((tmp_path/'OUT').rglob('*'))
        assert record.events[-1].state == 'cancelled'
    finally:
        release.set()
        qtbot.waitUntil(lambda: not operations.active, timeout=10000)
        for record in operations.records.values():
            if record.dialog:
                qtbot.addWidget(record.dialog)
                record.dialog.close()
        server.shutdown()
        thread.join()
