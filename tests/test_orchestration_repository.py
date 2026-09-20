from __future__ import annotations

import json
import sqlite3

import pytest

from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.repository import OrchestrationRepository
from kajovo.core.orchestration.work_order import WorkOrder


def _order(run_id: str, task_id: str, attempt_id: str) -> WorkOrder:
    return WorkOrder(
        version=2,
        run_id=run_id,
        step_id=task_id,
        task_id=task_id,
        stage="TEST",
        route="responses_live",
        target_id=task_id,
        target_path=None,
        expected_target_hash=None,
        input_projection_hash="input-" + task_id,
        contract_name="TEST_V1",
        schema_hash="schema-" + task_id,
        prompt_hash="prompt-" + task_id,
        model="gpt-5.6-luna",
        model_capability_hash="cap-" + task_id,
        policy_hash="policy-" + task_id,
        source_snapshot_hash="source-" + task_id,
        attempt_id=attempt_id,
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
        order, body_ref="body", input_hash=order.input_projection_hash
    )
    assert repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=work_hash,
        endpoint="/v1/responses",
        request_hash="request",
    )
    repo.mark_submission_started(order.attempt_id)
    with pytest.raises(OrchestrationError, match="DUPLICATE_SUBMIT_BLOCKED"):
        repo.prepare_provider_operation(
            attempt_id=order.attempt_id,
            work_order_hash=work_hash,
            endpoint="/v1/responses",
            request_hash="request",
        )


def test_raw_usage_is_idempotent_and_operation_completes(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    order = _order("RUN-1", "TASK-1", "ATTEMPT-1")
    work_hash = repo.register_work_order(
        order, body_ref="body", input_hash=order.input_projection_hash
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=work_hash,
        endpoint="/v1/responses",
        request_hash="request",
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
    assert "provider_operations" in tables
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
