"""Převzetí dávky nesmí měnit či znovu zapečetit poškozený Run Bundle."""
import json
from unittest.mock import Mock

import pytest

from kajovo.core.batch_completion import complete_saved_batch
from kajovo.core.config import AppSettings
from kajovo.core.contracts import ContractError
from kajovo.core.run_bundle import RunBundle
from kajovo.core.runs.locking import ExecutionLock


@pytest.mark.parametrize('damage', ['changed_artifact', 'foreign_metadata', 'missing_manifest'])
def test_batch_import_rejects_damaged_sealed_bundle_before_provider(tmp_path, damage):
    bundle = RunBundle(tmp_path/'RUN_BATCH', 'RUN_BATCH', create=True)
    state = {'mode': 'GENERATE', 'status': 'batch_pending', 'batch_id': 'batch_owned',
             'out_dir': str(tmp_path/'OUT'), 'ui_state': {}, 'batch_records': {}, 'batch_imports': {}}
    (bundle.root/'run_state.json').write_text(json.dumps(state), encoding="utf-8")
    with ExecutionLock(bundle.root/'execution.lock'):
        pass
    source = bundle.root/'artifacts/inputs/source.txt'
    source.write_bytes(b'schvaleny obsah')
    bundle.seal()
    assert bundle.verify_integrity()['valid']
    if damage == 'changed_artifact':
        source.write_bytes(b'zameneny obsah')
    elif damage == 'foreign_metadata':
        metadata = json.loads(bundle.bundle_path.read_text(encoding="utf-8"))
        metadata['run_id'] = 'RUN_FOREIGN'
        bundle.bundle_path.write_text(json.dumps(metadata), encoding="utf-8")
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


def test_recovered_unknown_submission_keeps_sealed_source_valid(tmp_path):
    from change_v2_fixtures import scenario, run
    from kajovo.core.batch_completion import read_state, recover_unknown_submission

    worker, client, _ = scenario(tmp_path, 'GENERATE', batch=True, maximum_quality=False)
    client.create_batch.side_effect = RuntimeError('Spojení přerušeno po přijetí')
    _, errors = run(worker, client)
    assert errors
    root = worker.log.paths.run_dir
    bundle = RunBundle(root)
    assert bundle.verify_integrity()['valid']
    state = read_state(root)
    batch = {'id': 'batch_recovered', 'input_file_id': state['batch_input_file_id'],
             'endpoint': '/v1/responses', 'status': 'completed', 'output_file_id': 'file_out'}
    assert recover_unknown_submission(root, [batch]) == batch
    assert bundle.verify_integrity()['valid'], 'Legitimní recovery nesmí zneplatnit manifest'
    assert client.create_batch.call_count == 1


def test_refresh_of_sealed_batch_preserves_manifest(tmp_path):
    from kajovo.core.batch_completion import remember_remote_batch_state

    bundle = RunBundle(tmp_path/'RUN_REFRESH', 'RUN_REFRESH', create=True)
    state = {'mode': 'GENERATE', 'status': 'batch_pending', 'batch_id': 'batch_owned',
             'ui_state': {}, 'batch_records': {}, 'batch_imports': {}}
    (bundle.root/'run_state.json').write_text(json.dumps(state), encoding="utf-8")
    bundle.seal()
    assert bundle.verify_integrity()['valid']
    assert remember_remote_batch_state(bundle.root, {'id': 'batch_owned', 'status': 'completed'})
    assert bundle.verify_integrity()['valid'], 'Refresh nesmí zneplatnit existující manifest'


def test_batch_panel_keeps_healthy_run_when_other_bundle_is_corrupt(qtbot, tmp_path):
    from kajovo.studio.batches import BatchesPage
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations

    bundles = []
    for suffix in ['BAD', 'GOOD']:
        bundle = RunBundle(tmp_path/'LOG'/('RUN_'+suffix), 'RUN_'+suffix, create=True)
        state = {'mode': 'GENERATE', 'status': 'batch_pending', 'batch_id': 'batch_'+suffix,
                 'ui_state': {}, 'batch_records': {}, 'batch_imports': {}}
        (bundle.root/'run_state.json').write_text(json.dumps(state), encoding="utf-8")
        bundle.seal()
        bundles.append(bundle)
    metadata = json.loads(bundles[0].bundle_path.read_text(encoding="utf-8"))
    metadata['run_id'] = 'RUN_FOREIGN'
    bundles[0].bundle_path.write_text(json.dumps(metadata), encoding="utf-8")
    bad_before = {str(p.relative_to(bundles[0].root)): p.read_bytes() for p in bundles[0].root.rglob('*') if p.is_file()}
    client = Mock()
    client.list_batches.return_value = [{'id': 'batch_'+s, 'status': 'completed'} for s in ['BAD', 'GOOD']]
    operations = Operations(None)
    context = StudioContext(AppSettings(log_dir=str(tmp_path/'LOG'), cache_dir=str(tmp_path/'cache')),
                            operations, api_key='synthetic', client_factory=lambda *a, **k: client)
    page = BatchesPage(context)
    operations.setParent(page)
    qtbot.addWidget(page)
    page.refresh_button.click()
    qtbot.waitUntil(lambda: not operations.active and not page.busy, timeout=10000)
    assert len(page.records) == 2, 'Vadný bundle nesmí zablokovat zdravou dávku'
    assert 'Část dávek se nepodařilo' in page.notice.text()
    assert next(r for r in page.records if r['id'] == 'batch_GOOD')['remote']['status'] == 'completed'
    assert bundles[1].verify_integrity()['valid']
    assert bad_before == {str(p.relative_to(bundles[0].root)): p.read_bytes() for p in bundles[0].root.rglob('*') if p.is_file() and p.name != 'execution.lock'}
    for record in operations.records.values():
        if record.dialog:
            qtbot.addWidget(record.dialog)
            record.dialog.close()
