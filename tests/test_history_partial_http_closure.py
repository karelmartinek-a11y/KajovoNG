"""History přebírá částečné výrobní artefakty bez opakovaného submitu."""
import json
from unittest.mock import patch

import pytest
import requests

from test_delivery_http_graph import RecordingHttp, graph_files, expected_content
from test_qa_qfile_http_closure import http_client
from test_runtime_end_to_end import child
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


def partial_source(root, mode):
    from pathlib import Path
    from change_v2_fixtures import scenario
    from kajovo.core.run_bundle import LegacyRunAdapter
    transport = PartialHttp(root, mode)
    worker, _, _ = scenario(root, mode, maximum_quality=True, files=graph_files(mode))
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch('kajovo.core.runs.executor.OpenAIClient', lambda *a, **k: http_client(transport)):
        worker.run()
    assert errors and not results
    adapter = LegacyRunAdapter(worker.log.paths.run_dir)
    state = json.loads((adapter.root / 'run_state.json').read_text())
    assert state['status'] == 'failed'
    rows = state['staged_files']
    assert [row['path'] for row in rows] == ['middle.txt', 'seed.txt']
    for row in rows:
        assert (adapter.root / row['staged_path']).read_bytes() == expected_content(mode, row['path']).encode()
    assert not list(Path(worker.cfg.out_dir).glob('*.txt'))
    assert adapter.bundle.verify_integrity()['valid']
    (root / 'history-source.json').write_text(json.dumps({'run': str(adapter.root), 'out': worker.cfg.out_dir}))


@pytest.mark.parametrize('mode', ['GENERATE', 'MODIFY'])
@pytest.mark.parametrize('relation', ['continue', 'repair'])
def test_history_partial_artifacts_new_process_ui_http(tmp_path, mode, relation):
    child(f"from pathlib import Path; import sys; from test_history_partial_http_closure import partial_source; partial_source(Path(sys.argv[1]), {mode!r})", tmp_path)
    child(f"from pathlib import Path; import sys; from test_history_checkpoint_http_closure import branch_process; branch_process(Path(sys.argv[1]), {mode!r}, '2Q', {relation!r}, True)", tmp_path)
