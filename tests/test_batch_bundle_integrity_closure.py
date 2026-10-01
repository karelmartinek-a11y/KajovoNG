"""Převzetí dávky nesmí měnit či znovu zapečetit poškozený Run Bundle."""
import json
from unittest.mock import Mock

import pytest

from kajovo.core.batch_completion import complete_saved_batch
from kajovo.core.config import AppSettings
from kajovo.core.contracts import ContractError
from kajovo.core.run_bundle import RunBundle


@pytest.mark.parametrize('damage', ['changed_artifact', 'foreign_metadata', 'missing_manifest'])
def test_batch_import_rejects_damaged_sealed_bundle_before_provider(tmp_path, damage):
    bundle = RunBundle(tmp_path/'RUN_BATCH', 'RUN_BATCH', create=True)
    state = {'mode': 'GENERATE', 'status': 'batch_pending', 'batch_id': 'batch_owned',
             'out_dir': str(tmp_path/'OUT'), 'ui_state': {}, 'batch_records': {}, 'batch_imports': {}}
    (bundle.root/'run_state.json').write_text(json.dumps(state))
    (bundle.root/'execution.lock').touch()
    source = bundle.root/'artifacts/inputs/source.txt'
    source.write_bytes(b'schvaleny obsah')
    bundle.seal()
    assert bundle.verify_integrity()['valid']
    if damage == 'changed_artifact':
        source.write_bytes(b'zameneny obsah')
    elif damage == 'foreign_metadata':
        metadata = json.loads(bundle.bundle_path.read_text())
        metadata['run_id'] = 'RUN_FOREIGN'
        bundle.bundle_path.write_text(json.dumps(metadata))
    else:
        bundle.checksums_path.unlink()
    before = {str(p.relative_to(bundle.root)): p.read_bytes() for p in bundle.root.rglob('*') if p.is_file()}
    client = Mock()
    client.retrieve_batch.return_value = {'id': 'batch_owned', 'status': 'in_progress'}
    with pytest.raises(ContractError, match='integrit'):
        complete_saved_batch(client, bundle.root, 'batch_owned', AppSettings())
    assert client.mock_calls == []
    assert before == {str(p.relative_to(bundle.root)): p.read_bytes() for p in bundle.root.rglob('*') if p.is_file()}
    assert not (tmp_path/'OUT').exists()
