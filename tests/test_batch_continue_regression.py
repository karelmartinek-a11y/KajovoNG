from pathlib import Path
from unittest.mock import Mock, patch

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

    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()

    assert not errors
    assert results[-1]["batch_id"] == "batch_work"
    client.create_response.assert_not_called()
    client.preflight_run.assert_not_called()
    client.create_batch.assert_called_once()
    assert Path(client.upload_file.call_args.args[0]).read_bytes()
