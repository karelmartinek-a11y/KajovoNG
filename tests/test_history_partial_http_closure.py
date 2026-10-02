"""History přebírá částečné výrobní artefakty bez opakovaného submitu."""
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
import requests

from test_delivery_http_graph import RecordingHttp, graph_files, expected_content
from test_qa_qfile_http_closure import http_client
from test_runtime_end_to_end import child
from test_runtime_end_to_end import ROOT
from change_v2_fixtures import _file_input_json


class PartialHttp(RecordingHttp):
    def request(self, method, url, **kwargs):
        body = kwargs.get('json') or {}
        if url.endswith('/responses') and body['text']['format']['name'] == 'FILE_CONTENT_V1':
            data = json.loads(body['input']) if isinstance(body['input'], str) else _file_input_json(body)
            if data['file_context']['working_context']['target_file']['path'] == 'final.txt':
                self.calls.append({'method': method, 'path': '/responses', 'body': body})
                response = requests.Response()
                response.status_code = 400
                response._content = b'{"error":{"message":"Synthetic rejected last file"}}'
                response.headers['Content-Type'] = 'application/json'
                return response
        return super().request(method, url, **kwargs)


def partial_source(root, mode, crash=False):
    from pathlib import Path
    from change_v2_fixtures import scenario
    from kajovo.core.run_bundle import LegacyRunAdapter
    from kajovo.core.run_bundle import RunBundle
    transport = PartialHttp(root, mode)
    worker, _, _ = scenario(root, mode, maximum_quality=True, files=graph_files(mode))
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    original = RunBundle.archive_artifact
    from kajovo.core.recoverable_artifacts import save_artifact
    def saved(run_dir, name, data):
        value = save_artifact(run_dir, name, data)
        if crash == 'before_staging' and name.startswith('generated/'):
            (root / 'history-source.json').write_text(json.dumps({'run': str(run_dir), 'out': worker.cfg.out_dir}), encoding="utf-8")
            os._exit(90)
        return value
    def archived(bundle, path, **kwargs):
        value = original(bundle, path, **kwargs)
        if crash is True and kwargs.get('role') == 'staged_output':
            (root / 'history-source.json').write_text(json.dumps({'run': str(bundle.root), 'out': worker.cfg.out_dir}), encoding="utf-8")
            os._exit(89)
        return value
    with patch('kajovo.core.runs.executor.OpenAIClient', lambda *a, **k: http_client(transport)), patch.object(RunBundle, 'archive_artifact', archived), patch('kajovo.core.recoverable_artifacts.save_artifact', saved):
        worker.run()
    assert errors and not results
    adapter = LegacyRunAdapter(worker.log.paths.run_dir)
    state = json.loads((adapter.root / 'run_state.json').read_text(encoding="utf-8"))
    assert state['status'] == 'failed'
    rows = state['staged_files']
    assert [row['path'] for row in rows] == ['middle.txt', 'seed.txt']
    for row in rows:
        assert (adapter.root / row['staged_path']).read_bytes() == expected_content(mode, row['path']).encode()
    assert not list(Path(worker.cfg.out_dir).glob('*.txt'))
    assert adapter.bundle.verify_integrity()['valid']
    (root / 'history-source.json').write_text(json.dumps({'run': str(adapter.root), 'out': worker.cfg.out_dir}), encoding="utf-8")


@pytest.mark.parametrize('mode', ['GENERATE', 'MODIFY'])
@pytest.mark.parametrize('relation', ['continue', 'repair'])
def test_history_partial_artifacts_new_process_ui_http(tmp_path, mode, relation):
    child(f"from pathlib import Path; import sys; from test_history_partial_http_closure import partial_source; partial_source(Path(sys.argv[1]), {mode!r})", tmp_path)
    child(f"from pathlib import Path; import sys; from test_history_checkpoint_http_closure import branch_process; branch_process(Path(sys.argv[1]), {mode!r}, '2Q', {relation!r}, True)", tmp_path)


@pytest.mark.parametrize('mode', ['GENERATE', 'MODIFY'])
@pytest.mark.parametrize('phase, code', [(True, 89), ('before_staging', 90)])
def test_history_crash_after_artifact_before_state_reuses_paid_bytes(tmp_path, mode, phase, code):
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'tests')]))
    script = f"from pathlib import Path; import sys; from test_history_partial_http_closure import partial_source; partial_source(Path(sys.argv[1]), {mode!r}, {phase!r})"
    first = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert first.returncode == code, first.stdout + first.stderr
    child(f"from pathlib import Path; import sys; from test_history_checkpoint_http_closure import branch_process; branch_process(Path(sys.argv[1]), {mode!r}, '2Q', 'continue', True, True)", tmp_path)


@pytest.mark.parametrize('field', ['graph', 'response', 'target', 'checksum'])
def test_uncommitted_read_model_cannot_import_foreign_artifact_binding(tmp_path, field):
    from pathlib import Path
    from kajovo.core.orchestration.manual_resources import recoverable_staged_files
    from kajovo.core.contracts import ContractError
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'tests')]))
    code = "from pathlib import Path; import sys; from test_history_partial_http_closure import partial_source; partial_source(Path(sys.argv[1]), 'GENERATE', True)"
    first = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert first.returncode == 89, first.stdout + first.stderr
    root = Path(json.loads((tmp_path / 'history-source.json').read_text(encoding="utf-8"))['run'])
    from kajovo.core.run_bundle import RunBundle
    bundle = RunBundle(root)
    path = bundle.artifact_index_path
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    artifact = next(row for row in rows if row.get('role') == 'staged_output')
    binary = (root / artifact['path_in_bundle']).read_bytes()
    if field == 'response':
        artifact['source_response_id'] = 'resp_foreign'
    else:
        key = {'graph': 'implementation_graph_hash', 'target': 'expected_target_hash', 'checksum': 'sha256'}[field]
        artifact['metadata'][key] = 'foreign'
    path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding="utf-8")
    with pytest.raises(ContractError, match='nepatří původnímu'):
        recoverable_staged_files(root)
    assert (root / artifact['path_in_bundle']).read_bytes() == binary
