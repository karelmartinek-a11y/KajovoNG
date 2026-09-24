from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

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


def test_manual_attempt_after_third_preserves_automatic_limit(tmp_path):
    from kajovo.core.orchestration.work_order import validate_work_order_v2
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    first = _order("RUN-1", "TASK-1", "unused")
    order = replace(first, version=3, attempt_kind="manual", attempt_no=4,
                    attempt_id=attempt_identity("RUN-1", "TASK-1", 4))
    validate_work_order_v2(order.to_dict())
    repo.register_work_order(order, body_ref=canonical_sha256("body"), input_hash=order.input_projection_hash)
    with pytest.raises(ValueError):
        validate_work_order_v2(replace(order, attempt_kind="automatic").to_dict())
    with pytest.raises(ValueError):
        validate_work_order_v2(replace(order, version=2, attempt_kind=None).to_dict())


def test_latest_target_attempt_uses_frozen_work_order_and_is_run_scoped(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    for run_id in ("RUN-1", "RUN-2"):
        _run(repo, run_id)
        for attempt in range(1, 4 if run_id == "RUN-2" else 3):
            order = replace(_order(run_id, "file", "unused"), target_path="a.txt",
                            attempt_no=attempt, attempt_id=attempt_identity(run_id, "file", attempt))
            repo.register_work_order(order, body_ref=canonical_sha256("body"), input_hash=order.input_projection_hash)
    assert repo.latest_target_attempts("RUN-1") == {"a.txt": 2}
    assert repo.latest_target_attempts("RUN-2") == {"a.txt": 3}
    assert repo.latest_target_attempts("unknown") == {}


def test_batch_dispatch_is_atomic_and_prepared_recovery_needs_no_upload(tmp_path):
    from kajovo.core.orchestration.batch_recovery import reconcile_unsubmitted
    run_dir = tmp_path / "RUN-1"
    run_dir.mkdir()
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    order = _order("RUN-1", "file", "unused")
    body_hash = canonical_sha256("body")
    work_hash = repo.register_work_order(order, body_ref=body_hash, input_hash=order.input_projection_hash)
    repo.prepare_provider_operation(attempt_id=order.attempt_id, work_order_hash=work_hash,
                                    endpoint="/v1/responses", request_hash=body_hash)
    with pytest.raises(OrchestrationError):
        repo.mark_batch_dispatch([order.attempt_id, "absent"], rejected=True)
    with repo.connect() as db:
        assert db.execute("SELECT state FROM provider_operations").fetchone()[0] == "prepared"
    state = {"submission_unknown": True, "status": "submission_unknown",
             "pending_batch_submission": {"manifest": {"work_orders": {"file": order.to_dict()}}}}
    reconcile_unsubmitted(run_dir, state)
    assert state["submission_unknown"] is False
    assert "pending_batch_submission" not in state
    assert state["status"] == "partial"
    with repo.connect() as db:
        assert db.execute("SELECT state FROM provider_operations").fetchone()[0] == "not_submitted"


def test_attempt_migration_preserves_historical_json_and_foreign_keys(tmp_path, monkeypatch):
    from kajovo.core.orchestration.repository import _SCHEMA
    path = tmp_path / "orchestration.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_SCHEMA.replace("CHECK(attempt_no >= 1)", "CHECK(attempt_no BETWEEN 1 AND 3)"))
    with monkeypatch.context() as patch:
        patch.setattr("kajovo.core.orchestration.repository._migrate_attempt_ordinals", lambda db: None)
        repo = OrchestrationRepository(path)
        _run(repo, "RUN-1")
        old = _order("RUN-1", "TASK-1", "unused")
        work_hash = repo.register_work_order(old, body_ref=canonical_sha256("body"), input_hash=old.input_projection_hash)
        repo.prepare_provider_operation(attempt_id=old.attempt_id, work_order_hash=work_hash,
                                        endpoint="/v1/responses", request_hash=canonical_sha256("body"))
    with repo.connect() as db:
        before = db.execute("SELECT * FROM work_orders").fetchall()
        operations = db.execute("SELECT * FROM provider_operations").fetchall()
    OrchestrationRepository(path)
    with repo.connect() as db:
        assert db.execute("SELECT * FROM work_orders").fetchall() == before
        assert db.execute("SELECT * FROM provider_operations").fetchall() == operations
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert "BETWEEN 1 AND 3" not in db.execute("SELECT sql FROM sqlite_master WHERE name='work_orders'").fetchone()[0]


def test_terminal_provider_failure_without_usage_is_closed(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    order = _order("RUN-1", "TASK-1", "unused")
    body_hash = canonical_sha256("body")
    work_hash = repo.register_work_order(order, body_ref=body_hash, input_hash=order.input_projection_hash)
    repo.prepare_provider_operation(attempt_id=order.attempt_id, work_order_hash=work_hash,
                                    endpoint="/v1/responses", request_hash=body_hash)
    repo.mark_submitted(order.attempt_id, "resp_failed", unknown=False)
    with pytest.raises(OrchestrationError):
        repo.mark_terminal(order.attempt_id, "resp_other")
    repo.mark_terminal(order.attempt_id, "resp_failed")
    repo.mark_terminal(order.attempt_id, "resp_failed")
    with repo.connect() as db:
        assert db.execute("SELECT state FROM provider_operations").fetchone()[0] == "completed"
        assert db.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0] == 0


def test_batch_identity_recovery_is_atomic_on_conflicting_second_order(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-1")
    orders = []
    for task in ("ONE", "TWO"):
        order = replace(_order("RUN-1", task, "unused"), route="responses_batch", provider_endpoint="/v1/batches")
        body_hash = canonical_sha256(task)
        work_hash = repo.register_work_order(order, body_ref=body_hash, input_hash=order.input_projection_hash)
        repo.prepare_provider_operation(attempt_id=order.attempt_id, work_order_hash=work_hash,
                                        endpoint="/v1/batches", request_hash=body_hash)
        repo.set_remote_input_file(order.attempt_id, "file_input")
        orders.append(order)
    repo.mark_submitted(orders[1].attempt_id, "batch_other", unknown=False)
    with pytest.raises(OrchestrationError, match="PROVIDER_ID_CONFLICT"):
        repo.recover_batch_identity(orders, batch_id="batch_correct", input_file_id="file_input")
    with repo.connect() as db:
        assert db.execute("SELECT state,provider_id FROM provider_operations WHERE attempt_id=?", (orders[0].attempt_id,)).fetchone() == ("prepared", None)


def test_manual_authorization_can_refresh_expiry_without_enabling_auto_repair():
    from kajovo.core.orchestration.authorization import create_execution_authorization, validate_execution_authorization
    cfg = {"model_bindings": [], "quality": "standard", "auto_repair": "off", "verification_profile_ids": [],
           "execution": "batch", "stop_after_plan": False, "dry_run": False}
    authorization = create_execution_authorization("RUN", cfg, "scope", lifetime_hours=-1).to_dict()
    with pytest.raises(ValueError, match="expired"):
        validate_execution_authorization(authorization, run_id="RUN", run_config=cfg, scope_hash="scope")
    restored = validate_execution_authorization(authorization, run_id="RUN", run_config=cfg, scope_hash="scope", allow_expired=True)
    assert restored.repair_allowed is False
    with pytest.raises(ValueError, match="repair"):
        validate_execution_authorization(authorization, run_id="RUN", run_config=cfg, scope_hash="scope", allow_expired=True, require_repair=True)


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



def test_usage_cannot_complete_unconfirmed_provider_operation(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-USAGE-GATE")
    order = _order("RUN-USAGE-GATE", "TASK-USAGE-GATE", "unused")
    request_hash = canonical_sha256({"payload": "usage-gate"})
    work_hash = repo.register_work_order(
        order,
        body_ref=request_hash,
        input_hash=order.input_projection_hash,
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=work_hash,
        endpoint=order.provider_endpoint,
        request_hash=request_hash,
    )
    with pytest.raises(OrchestrationError, match="PROVIDER_OPERATION_NOT_CONFIRMED"):
        repo.record_usage(
            order.attempt_id,
            provider="openai",
            provider_item_id="resp-unconfirmed",
            usage={},
        )


def test_remote_input_file_binding_is_idempotent_and_conflict_safe(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    _run(repo, "RUN-REMOTE-FILE")
    order = _order("RUN-REMOTE-FILE", "TASK-REMOTE-FILE", "unused")
    request_hash = canonical_sha256({"payload": "batch"})
    work_hash = repo.register_work_order(
        order,
        body_ref=request_hash,
        input_hash=order.input_projection_hash,
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=work_hash,
        endpoint=order.provider_endpoint,
        request_hash=request_hash,
    )
    repo.set_remote_input_file(order.attempt_id, "file-1")
    repo.set_remote_input_file(order.attempt_id, "file-1")
    with pytest.raises(OrchestrationError, match="REMOTE_INPUT_FILE_CONFLICT"):
        repo.set_remote_input_file(order.attempt_id, "file-2")
