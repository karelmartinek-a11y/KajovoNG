"""Kontrolní metadata bundle musí patřit k zapečetěnému run/manifestu."""
import json

import pytest

from kajovo.core.run_bundle import RunBundle


@pytest.mark.parametrize('field,value', [
    ('run_id', 'RUN_FOREIGN'), ('bundle_id', 'bundle_foreign'),
    ('bundle_hash', 'foreign_hash'), ('run_record_sha256', 'foreign_hash'),
    ('run_id', None), ('bundle_id', None), ('bundle_hash', None),
    ('run_record_sha256', None),
])
def test_sealed_bundle_rejects_foreign_or_missing_control_binding(tmp_path, field, value):
    bundle = RunBundle(tmp_path/'RUN_BINDING', 'RUN_BINDING', create=True)
    (bundle.root/'output.txt').write_bytes(b'preserved bytes')
    bundle.seal()
    assert bundle.verify_integrity()['valid']
    original = bundle.checksums_path.read_bytes()
    metadata = json.loads(bundle.bundle_path.read_text())
    if value is None:
        metadata.pop(field)
    else:
        metadata[field] = value
    bundle.bundle_path.write_text(json.dumps(metadata), encoding='utf-8')
    after = bundle.bundle_path.read_bytes()
    reopened = RunBundle(bundle.root)
    result = reopened.verify_integrity()
    assert not result['valid'], f'Foreign or missing {field} accepted as verified: {result}'
    assert result['status'] == 'changed' and result['errors']
    assert bundle.bundle_path.read_bytes() == after
    assert bundle.checksums_path.read_bytes() == original
    assert (bundle.root/'output.txt').read_bytes() == b'preserved bytes'


@pytest.mark.parametrize('field', ['run_id', 'bundle_id', 'bundle_hash', 'run_record_sha256'])
def test_history_new_process_blocks_foreign_bundle_before_submit(tmp_path, field):
    from test_runtime_end_to_end import child

    child("from pathlib import Path; import sys; from test_delivery_http_graph import delivery_process; "
          "delivery_process(Path(sys.argv[1]), 'GENERATE', False, 'prepare')", tmp_path)
    info = json.loads((tmp_path/'delivery-info.json').read_text())
    from pathlib import Path
    root = Path(info['run'])
    metadata_path = root/'bundle.json'
    metadata = json.loads(metadata_path.read_text())
    metadata[field] = 'foreign_identity'
    metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
    original = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    requests = (tmp_path/'http-calls.jsonl').read_bytes()
    child("from pathlib import Path; import sys; from test_additional_process_recovery import history_process; "
          "root=Path(sys.argv[1])\n"
          "try:\n history_process(root, 'continue')\n"
          "except ValueError as error:\n"
          " assert 'Zdrojový Run Bundle neprošel kontrolou integrity' in str(error), str(error)\n"
          " (root/'blocked-result.txt').write_text(str(error), encoding='utf-8')\n"
          "else:\n raise AssertionError('Foreign bundle metadata allowed History submit')", tmp_path)
    assert 'kontrolou integrity' in (tmp_path/'blocked-result.txt').read_text()
    assert (tmp_path/'http-calls.jsonl').read_bytes() == requests
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == original
