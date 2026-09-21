from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from test_generate_batch import manifest
from test_workflows import make_worker


def test_continue_generate_batch_with_recovered_resume_files_skips_live_a1_a2(tmp_path):
    """Regrese: Pokračovat po hotovém preflightu nesmí spadnout na resume_files guardu."""
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.send_as_c = True
    saved = manifest()
    worker.cfg.resume_files = list(saved["snapshot"]["structure"]["files"])
    worker.resume_generate_batch = saved

    client = Mock()
    client.upload_file.return_value = {"id": "file_work"}
    client.create_batch.return_value = {"id": "batch_work", "status": "validating"}
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client):
        worker.run()

    assert not errors
    assert results[-1]["batch_id"] == "batch_work"
    client.create_response.assert_not_called()
    client.preflight_run.assert_not_called()
    client.create_batch.assert_called_once()
    assert Path(client.upload_file.call_args.args[0]).read_bytes()


@pytest.mark.parametrize("field", ["model", "approval_id", "target_path", "run_id"])
def test_changed_work_order_blocks_recovery_before_upload(tmp_path, field):
    worker = make_worker(tmp_path, "GENERATE")
    saved = manifest()
    order = next(iter(saved["work_orders"].values()))
    order[field] = "pozměněná-hodnota"
    client = Mock()
    from kajovo.core.contracts import ContractError

    with pytest.raises(ContractError):
        worker._submit_generate_batch(client, saved)
    client.upload_file.assert_not_called()
    client.create_batch.assert_not_called()
    assert not (Path(worker.log.paths.run_dir).parent / "orchestration.sqlite3").exists()



def test_corrupt_current_batch_state_never_resets_to_empty_before_submit(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    state_path = Path(worker.log.state_path)
    state_path.write_text(
        '{"status":"running","status":"batch_pending"}',
        encoding="utf-8",
    )
    client = Mock()
    saved = manifest()
    from kajovo.core.contracts import ContractError
    with pytest.raises(ContractError):
        worker._submit_generate_batch(client, saved)
    client.upload_file.assert_not_called()
    client.create_batch.assert_not_called()
