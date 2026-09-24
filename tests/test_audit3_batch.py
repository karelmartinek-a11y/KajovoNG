"""Obnova prvního submitu a fyzické vazby jednoznačně odmítnuté dávky."""
from dataclasses import replace

import pytest
from change_v2_fixtures import scenario, run, batch_output_rows, raw_jsonl
from test_orchestration_repository import _order, _run
from kajovo.core.batch_completion import recover_unknown_submission, read_state, complete_saved_batch
from kajovo.core.orchestration.contracts import canonical_sha256
from kajovo.core.orchestration.repository import OrchestrationRepository
from kajovo.core.orchestration.errors import OrchestrationError


def test_first_unknown_batch_is_bound_before_import(tmp_path):
    worker, client, _ = scenario(tmp_path, "GENERATE", batch=True, maximum_quality=False)
    client.create_batch.side_effect = RuntimeError("Spojení přerušeno")
    _, errors = run(worker, client)
    assert errors
    root = worker.log.paths.run_dir
    state = read_state(root)
    batch = {"id": "batch_recovered", "input_file_id": state["batch_input_file_id"],
             "endpoint": "/v1/responses", "status": "completed", "output_file_id": "file_out"}
    assert "pending_batch_submission" not in state
    assert recover_unknown_submission(root, [batch]) == batch
    recovered = read_state(root)
    assert recovered["batch_manifest_v4"]["state"] == "submitted"
    assert recovered["batch_manifest_v4"]["provider_batch_id"] == batch["id"]
    client.retrieve_batch.return_value = batch
    client.file_content.return_value = raw_jsonl(batch_output_rows(state["generate_batch"]))
    result = complete_saved_batch(client, root, batch["id"], worker.settings)
    assert result["status"] == "files_complete_unverified"
    assert client.create_batch.call_count == 1


@pytest.mark.parametrize("rejected", [True, False])
def test_only_definite_rejection_can_release_batch_physical_binding(tmp_path, rejected):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    order = replace(_order("RUN-1", "file", "unused"), route="responses_batch", provider_endpoint="/v1/batches")
    digest = canonical_sha256("body")
    work_hash = repo.register_work_order(order, body_ref=digest, input_hash=order.input_projection_hash)
    args = dict(attempt_id=order.attempt_id, work_order_hash=work_hash, endpoint="/v1/batches", request_hash=digest)
    repo.prepare_provider_operation(**args)
    repo.bind_physical_request(order.attempt_id, physical_request_hash=canonical_sha256("first"), remote_input_file_id="file_first")
    if rejected:
        repo.mark_not_submitted(order.attempt_id)
    repo.prepare_provider_operation(**args)
    if rejected:
        repo.bind_physical_request(order.attempt_id, physical_request_hash=canonical_sha256("second"), remote_input_file_id="file_second")
    else:
        with pytest.raises(OrchestrationError):
            repo.bind_physical_request(order.attempt_id, physical_request_hash=canonical_sha256("second"), remote_input_file_id="file_second")
