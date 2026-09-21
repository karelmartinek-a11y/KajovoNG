from __future__ import annotations

import json

import pytest

from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.repository import OrchestrationRepository
from kajovo.core.orchestration.contracts import canonical_sha256
from kajovo.core.orchestration.work_order import WorkOrder, attempt_identity


def _order(run_id: str, task_id: str, attempt_id: str) -> WorkOrder:
    return WorkOrder(
        version=2,
        run_id=run_id,
        step_id=task_id,
        task_id=task_id,
        stage="TEST",
        route="responses_live",
        provider_endpoint="/v1/responses",
        target_id=task_id,
        target_path=None,
        expected_target_hash=None,
        input_projection_hash=canonical_sha256("input-" + task_id),
        contract_name="TEST_V1",
        schema_hash=canonical_sha256("schema-" + task_id),
        prompt_hash=canonical_sha256("prompt-" + task_id),
        model="gpt-5.6-luna",
        model_capability_hash=canonical_sha256("cap-" + task_id),
        policy_hash=canonical_sha256("policy-" + task_id),
        source_snapshot_hash=canonical_sha256("source-" + task_id),
        attempt_id=attempt_identity(run_id, task_id, 1),
        approval_id="approval-1",
        attempt_no=1,
    )


def _run(repo: OrchestrationRepository, run_id: str) -> None:
    repo.register_run(
        run_id,
        lineage_id=run_id,
        scope_hash="scope",
        policy_hash="policy",
        config={"version": 2},
        approval_id="approval-1",
        status="running",
    )


def test_provider_operation_prevents_duplicate_submit(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    order = _order("RUN-1", "TASK-1", "ATTEMPT-1")
    work_hash = repo.register_work_order(
        order, body_ref=canonical_sha256("request"), input_hash=order.input_projection_hash
    )
    assert repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=work_hash,
        endpoint="/v1/responses",
        request_hash=canonical_sha256("request"),
    )
    repo.mark_submission_started(order.attempt_id)
    with pytest.raises(OrchestrationError, match="DUPLICATE_SUBMIT_BLOCKED"):
        repo.prepare_provider_operation(
            attempt_id=order.attempt_id,
            work_order_hash=work_hash,
            endpoint="/v1/responses",
            request_hash=canonical_sha256("request"),
        )


def test_confirmed_provider_submit_cannot_be_reopened(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    order = _order("RUN-1", "TASK-1", "ATTEMPT-1")
    work_hash = repo.register_work_order(
        order, body_ref=canonical_sha256("request"), input_hash=order.input_projection_hash
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=work_hash,
        endpoint="/v1/responses",
        request_hash=canonical_sha256("request"),
    )
    repo.mark_submission_started(order.attempt_id)
    repo.mark_submitted(order.attempt_id, "resp-1", unknown=False)

    with pytest.raises(OrchestrationError, match="PROVIDER_OPERATION_STATE"):
        repo.mark_not_submitted(order.attempt_id)
    with pytest.raises(OrchestrationError, match="PROVIDER_OPERATION_STATE"):
        repo.mark_submission_started(order.attempt_id)


def test_raw_usage_is_idempotent_and_operation_completes(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    order = _order("RUN-1", "TASK-1", "ATTEMPT-1")
    work_hash = repo.register_work_order(
        order, body_ref=canonical_sha256("request"), input_hash=order.input_projection_hash
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=work_hash,
        endpoint="/v1/responses",
        request_hash=canonical_sha256("request"),
    )
    repo.mark_submission_started(order.attempt_id)
    repo.mark_submitted(order.attempt_id, "resp-1", unknown=False)
    usage = {"input_tokens": 40, "output_tokens": 20}
    assert repo.record_usage(
        order.attempt_id,
        provider="openai",
        provider_item_id="resp-1",
        usage=usage,
    )
    assert not repo.record_usage(
        order.attempt_id,
        provider="openai",
        provider_item_id="resp-1",
        usage=usage,
    )
    with repo.connect() as db:
        state = db.execute(
            "SELECT state,provider_id FROM provider_operations WHERE attempt_id=?",
            (order.attempt_id,),
        ).fetchone()
        saved = db.execute(
            "SELECT usage_json FROM usage_records WHERE provider_item_id='resp-1'"
        ).fetchone()
    assert state == ("completed", "resp-1")
    assert json.loads(saved[0]) == usage


def test_new_schema_contains_only_nonfinancial_provider_evidence(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    with repo.connect() as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        provider_columns = {
            row[1] for row in db.execute("PRAGMA table_info(provider_operations)")
        }
        usage_columns = {
            row[1] for row in db.execute("PRAGMA table_info(usage_records)")
        }
        work_order_columns = {
            row[1] for row in db.execute("PRAGMA table_info(work_orders)")
        }
    assert "provider_operations" in tables
    assert work_order_columns >= {
        "work_order_hash",
        "attempt_id",
        "provider_endpoint",
        "work_order_json",
        "body_ref",
        "input_hash",
    }
    assert provider_columns >= {
        "attempt_id",
        "work_order_hash",
        "request_hash",
        "state",
        "provider_id",
        "remote_input_file_id",
    }
    assert usage_columns == {
        "provider",
        "provider_item_id",
        "attempt_id",
        "usage_json",
    }



def test_work_order_persists_exact_canonical_json_and_endpoint(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-EXACT")
    order = _order("RUN-EXACT", "TASK-EXACT", "unused")
    request_hash = canonical_sha256({"payload": "exact"})
    work_hash = repo.register_work_order(
        order,
        body_ref=request_hash,
        input_hash=order.input_projection_hash,
    )
    with repo.connect() as db:
        row = db.execute(
            """
            SELECT work_order_json,attempt_id,provider_endpoint,body_ref
            FROM work_orders WHERE work_order_hash=?
            """,
            (work_hash,),
        ).fetchone()
    assert json.loads(row[0]) == order.to_dict()
    assert row[1] == order.attempt_id
    assert row[2] == order.provider_endpoint
    assert row[3] == request_hash


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("request_hash", "different", "PROVIDER_REQUEST_HASH_MISMATCH"),
        ("attempt_id", "ATTEMPT-different", "PROVIDER_ATTEMPT_MISMATCH"),
        ("endpoint", "/v1/batches", "PROVIDER_ENDPOINT_MISMATCH"),
    ],
)
def test_provider_operation_rejects_physical_binding_mismatch(
    tmp_path, field, value, code
):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-BIND")
    order = _order("RUN-BIND", "TASK-BIND", "unused")
    request_hash = canonical_sha256({"payload": "bound"})
    work_hash = repo.register_work_order(
        order,
        body_ref=request_hash,
        input_hash=order.input_projection_hash,
    )
    args = {
        "attempt_id": order.attempt_id,
        "work_order_hash": work_hash,
        "endpoint": order.provider_endpoint,
        "request_hash": request_hash,
    }
    args[field] = value
    with pytest.raises(OrchestrationError, match=code):
        repo.prepare_provider_operation(**args)
    with repo.connect() as db:
        assert db.execute(
            "SELECT 1 FROM provider_operations WHERE work_order_hash=?",
            (work_hash,),
        ).fetchone() is None
