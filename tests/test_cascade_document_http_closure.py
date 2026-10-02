"""Dokument kaskády přes Responses a Containers HTTP, přerušení a nový proces."""
import copy
from native_provider_fixtures import native_fixture
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from kajovo.core.cascade_contract import output_machine_key
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
from kajovo.core.cascade_types import CascadeDefinition, CascadeOutput, CascadeStep
from kajovo.core.config import AppSettings
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.runlog import RunLogger
from test_cascade_http_closure import client_for
from test_runtime_end_to_end import ROOT, child

MODEL = 'gpt-5.6-luna'


def artifact_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('obsah.txt', 'Přesný obsah dokumentu\n'.encode())
    return stream.getvalue()


class DocumentHttp:
    def __init__(self, root, output, fault=''):
        self.root, self.output, self.fault = root, output, fault
        self.calls = []
        self.raw = (root / 'artifact.zip').read_bytes() if (root / 'artifact.zip').exists() else artifact_bytes()
        (root / 'artifact.zip').write_bytes(self.raw)

    def request(self, method, url, **kwargs):
        path = url.split('/v1', 1)[1]
        body = copy.deepcopy(kwargs.get('json'))
        row = {'method':method, 'path':path, 'body':body}
        self.calls.append(row)
        with (self.root / 'document-http.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        raw, code = None, 200
        if path == '/models':
            value = {'data':[{'id':MODEL}], 'has_more':False}
        elif path == '/responses/input_tokens':
            value = {'input_tokens':128}
        elif path == '/responses':
            assert method == 'POST' and body['model'] == MODEL and 'previous_response_id' not in body
            if body['text']['format']['name'] == 'CASCADE_DOCUMENT_ARTIFACT_V1':
                assert body['tools'] == [{'type':'code_interpreter', 'container':{'type':'auto', 'file_ids':[]}}]
                assert body['tool_choice'] == 'required'
                assert json.loads(body['input'])['task']['path'] == self.output.file_name
                annotation = {'type':'container_file_citation', 'filename':self.output.file_name, 'container_id':'cntr_one', 'file_id':'cfile_one'}
                if self.fault == 'foreign':
                    annotation['container_id'] = 'cntr_foreign'
                value = {'id':'resp_document', 'status':'completed', 'model':MODEL, 'output':[
                    {'type':'code_interpreter_call', 'status':'completed', 'container_id':'cntr_one'},
                    {'type':'message', 'role':'assistant', 'status':'completed', 'content':[
                        {'type':'output_text', 'text':json.dumps({'filename':self.output.file_name}), 'annotations': [] if self.fault == 'missing' else [annotation]}]}]}
            else:
                value = {'id':'resp_primary', 'status':'completed', 'model':MODEL, 'output_text':json.dumps({output_machine_key(self.output):{'contract':'CASCADE_BINARY_TASK_V1','path':self.output.file_name, 'instructions':'Vytvoř archiv s českým textem'}})}
        elif path in {'/containers/cntr_one/files/cfile_one/content', '/containers/cntr_foreign/files/cfile_one/content'}:
            assert method == 'GET'
            if self.fault == 'expired':
                code, value = 404, {'error':{'message':'Expired synthetic container'}}
            else:
                raw = b'' if self.fault == 'empty' else self.raw
        elif (method,path) == ('POST','/files'):
            assert kwargs['data'] == {'purpose':'user_data'}
            assert kwargs['files']['file'][1].read() == self.raw
            value = {'id':'file_document', 'filename':self.output.file_name, 'bytes':len(self.raw)}
        elif path == '/files/file_document':
            value = {'id':'file_document', 'filename':self.output.file_name, 'bytes':len(self.raw)}
        else:
            raise AssertionError(f'Neočekávané HTTP {method} {path}')
        response = requests.Response()
        response.status_code = code
        response.headers['Content-Type'] = 'application/json' if raw is None else 'application/octet-stream'
        response._content = json.dumps(native_fixture(method, path, value, kwargs.get("json") or kwargs.get("data"))).encode() if raw is None else raw
        return response


def document_process(root, phase, fault=''):
    path = root / 'definition.json'
    if not path.exists():
        value = CascadeDefinition('Dokument HTTP', steps=[CascadeStep(title='Dokument', model=MODEL, input_text='Vyrob archiv', deterministic=True, outputs=[CascadeOutput(kind='file', file_type='zip', file_name='obsah.zip')])])
        path.write_text(json.dumps(value.to_dict()), encoding="utf-8")
    else:
        value = CascadeDefinition.from_dict(json.loads(path.read_text(encoding="utf-8")))
        value.run_from_step_id = value.steps[0].id
    transport = DocumentHttp(root, value.steps[0].outputs[0], fault)
    worker = CascadeRunExecutor(CascadeRunConfig('Dokument', value, '', str(root / 'OUT')), AppSettings(log_dir=str(root / 'LOG')), 'synthetic')
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    original = RunLogger.save_json
    def saved(logger, group, name, data, **kwargs):
        result = original(logger, group, name, data, **kwargs)
        if phase == 'crash' and name.startswith('cascade_binary_') and name.endswith('_artifact'):
            os._exit(88)
        return result
    with patch('kajovo.core.cascade_pipeline.OpenAIClient', lambda *a, **k: client_for(transport)), patch.object(RunLogger, 'save_json', saved):
        worker.execute()
    if fault:
        assert errors and not results, (errors, results)
        state = json.loads(Path(worker.logger.state_path).read_text(encoding="utf-8"))
        expected = {'foreign':'kontejneru', 'missing':'identitu artefaktu', 'empty':'obsah souboru', 'expired':'Expired synthetic container'}[fault]
        assert expected in state['error'], state['error']
        assert not (root / 'OUT' / 'obsah.zip').exists()
    else:
        assert results and not errors, errors
        assert (root / 'OUT' / 'obsah.zip').read_bytes() == transport.raw
        with zipfile.ZipFile(root / 'OUT' / 'obsah.zip') as archive:
            assert archive.read('obsah.txt') == 'Přesný obsah dokumentu\n'.encode()
        assert LegacyRunAdapter(worker.logger.paths.run_dir).bundle.verify_integrity()['valid']


@pytest.mark.parametrize('fault', ['', 'foreign', 'missing', 'empty', 'expired'])
def test_cascade_document_actual_transport_and_second_process(tmp_path, fault):
    if not fault:
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT / 'tests')]))
        result = subprocess.run([sys.executable,'-c',"from pathlib import Path; import sys; from test_cascade_document_http_closure import document_process; document_process(Path(sys.argv[1]), 'crash')", str(tmp_path)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
        assert result.returncode == 88, result.stdout + result.stderr
    else:
        child(f"from pathlib import Path; import sys; from test_cascade_document_http_closure import document_process; document_process(Path(sys.argv[1]), 'first', {fault!r})", tmp_path)
    child(f"from pathlib import Path; import sys; from test_cascade_document_http_closure import document_process; document_process(Path(sys.argv[1]), 'restart', {fault!r})", tmp_path)
    rows = [json.loads(line) for line in (tmp_path / 'document-http.jsonl').read_text(encoding="utf-8").splitlines()]
    assert sum(r['path'] == '/responses' for r in rows) == 2
    assert sum(r['path'] == '/files' for r in rows) == (0 if fault else 1)
    if fault in {'foreign','missing'}:
        assert not any(r['path'].startswith('/containers') for r in rows)
