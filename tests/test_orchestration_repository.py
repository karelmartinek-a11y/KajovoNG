from kajovo.core.orchestration.repository import OrchestrationRepository
from kajovo.core.orchestration.work_order import WorkOrder


def _order(run_id, task_id, reservation_id):
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
        budget_reservation_id=reservation_id,
        approval_id="approval-1",
        attempt_no=1,
    )


def test_settlement_reconciles_reserved_budget_to_actual_usage(tmp_path):
    repo = OrchestrationRepository(tmp_path / "orchestration.sqlite3")
    run_id = "RUN_TEST"
    repo.register_run(
        run_id,
        lineage_id=run_id,
        scope_hash="scope",
        policy_hash="policy",
        config={"version": 1},
        approval_id="approval-1",
        status="running",
    )

    first = _order(run_id, "TASK-1", "RES-1")
    repo.register_work_order(first, body_ref="body-1", input_hash="input-1")
    repo.reserve(
        reservation_id=first.budget_reservation_id,
        work_order_hash=first.order_hash,
        cost_microusd=500,
        input_limit=100,
        output_limit=1000,
        max_cost_microusd=10_000,
        max_input_tokens=10_000,
        max_output_tokens=1000,
        max_paid_requests=10,
    )
    repo.mark_submitted(first.budget_reservation_id, "resp-1", unknown=False)
    repo.settle(
        first.budget_reservation_id,
        provider="openai",
        provider_item_id="resp-1",
        usage={"input_tokens": 40, "output_tokens": 20},
        actual_cost_microusd=100,
        price_snapshot_hash="price-1",
    )

    with repo.connect() as db:
        row = db.execute(
            "SELECT state,cost_microusd,input_limit,output_limit,provider_id "
            "FROM reservations WHERE reservation_id='RES-1'"
        ).fetchone()
    assert row == ("settled", 100, 40, 20, "resp-1")

    # The next reservation is allowed because the first one now consumes
    # actual 20 output tokens rather than its original 1000-token ceiling.
    second = _order(run_id, "TASK-2", "RES-2")
    repo.register_work_order(second, body_ref="body-2", input_hash="input-2")
    assert repo.reserve(
        reservation_id=second.budget_reservation_id,
        work_order_hash=second.order_hash,
        cost_microusd=500,
        input_limit=100,
        output_limit=980,
        max_cost_microusd=10_000,
        max_input_tokens=10_000,
        max_output_tokens=1000,
        max_paid_requests=10,
    )
