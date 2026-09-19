"""Settlement musí uvolnit jen doloženou rezervu a zachovat rozpočtové brány."""
import copy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration import ledger
from kajovo.core.orchestration.repository import repository_for_logger
from test_orchestration_repository import _order
from test_workflows import make_worker


def reservation(worker, index, output=1000, input_tokens=100, *, batch=False, cost=None, run_id=None):
    order = replace(
        _order(run_id or worker.log.run_id, f"TASK-{index}", f"RES-{index}"),
        route="responses_batch" if batch else "responses_live",
    )
    payload = {"model": order.model, "input": str(index), "max_output_tokens": output}
    measurement = {
        "blockers": [], "input_tokens": input_tokens, "output_budget": output,
        "projected_cost": {"usd": cost / 1_000_000} if cost is not None else None,
    }
    if batch:
        ledger.reserve_batch(
            worker.log, worker.cfg, None,
            [{"custom_id": str(index), "body": payload}], [measurement],
            work_orders={str(index): order.to_dict()},
        )
    else:
        ledger.reserve_paid_request(
            worker.log, worker.cfg, None, payload,
            measurement=measurement, work_order=order,
        )
    return order


def summary(worker):
    return json.loads(Path(worker.log.state_path).read_text("utf-8"))["budget_ledger_summary"]


@pytest.mark.parametrize("batch", [False, True])
def test_settlement_allows_exact_remaining_budget_but_blocks_excess(tmp_path, batch):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_output_tokens = 1000
    worker.cfg.max_input_tokens = 100
    first = reservation(worker, 1)
    response = {
        "id": "resp-first", "usage": {
            "input_tokens": 40, "output_tokens": 20,
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }
    ledger.mark_submission(worker.log, first, response["id"], unknown=False)
    ledger.settle_usage(worker.log, first, response)
    before = summary(worker)
    ledger.settle_usage(worker.log, first, response)
    assert summary(worker) == before
    assert before["output_tokens_reserved"] == 20
    assert before["input_tokens_reserved"] == 40
    assert before["paid_requests_reserved"] == 1

    reservation(worker, 2, output=980, input_tokens=60, batch=batch)
    assert summary(worker)["output_tokens_reserved"] == 1000
    with pytest.raises(ContractError, match="max_output_tokens"):
        reservation(worker, 3, output=1, input_tokens=0, batch=batch)
    with pytest.raises(ContractError, match="max_input_tokens"):
        reservation(worker, 4, output=0, input_tokens=1, batch=batch)
    worker.cfg.max_paid_requests = 2
    with pytest.raises(ContractError, match="max_paid_requests"):
        reservation(worker, 5, output=0, input_tokens=0, batch=batch)
    with repository_for_logger(worker.log).connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 2


@pytest.mark.parametrize("usage", [None, {}, {"output_tokens": -1}, {"output_tokens": True}])
def test_missing_or_invalid_usage_keeps_output_reservation(tmp_path, usage):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_output_tokens = 1000
    first = reservation(worker, 1)
    ledger.settle_usage(worker.log, first, {"id": "resp-first", "usage": usage})
    assert summary(worker)["output_tokens_reserved"] == 1000
    with pytest.raises(ContractError, match="max_output_tokens"):
        reservation(worker, 2, output=1)


def test_unknown_submit_keeps_reservation_and_release_refreshes_summary(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_output_tokens = 1000
    first = reservation(worker, 1)
    ledger.mark_submission(worker.log, first, None, unknown=True)
    with pytest.raises(ContractError, match="max_output_tokens"):
        reservation(worker, 2, output=1)
    # Druhý, doloženě neodeslaný request může rezervaci uvolnit.
    worker.cfg.max_output_tokens = 2000
    second = reservation(worker, 2)
    ledger.release_reservation(worker.log, second)
    assert summary(worker)["output_tokens_reserved"] == 1000
    assert summary(worker)["paid_requests_reserved"] == 1


def test_batch_settlement_uses_reservation_identity(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_output_tokens = 1000
    reservation(worker, 0, output=0, input_tokens=0)
    first = reservation(worker, 1, batch=True)
    ledger.settle_usage(worker.log, first, {
        "id": "resp-batch", "usage": {"input_tokens": 40, "output_tokens": 20},
    })
    assert summary(worker)["output_tokens_reserved"] == 20
    reservation(worker, 2, output=980, batch=True)
    with pytest.raises(ContractError, match="max_output_tokens"):
        reservation(worker, 3, output=1, batch=True)


@pytest.mark.parametrize("legacy", [False, True])
def test_recovered_batch_settles_parent_order_only_by_explicit_id(tmp_path, legacy):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_output_tokens = 1000
    parent_run = "RUN_PARENT"
    repo = repository_for_logger(worker.log)
    repo.register_run(
        parent_run, lineage_id=parent_run, scope_hash="scope", policy_hash="policy",
        config={}, approval_id="approval-1", status="running",
    )
    first = reservation(worker, 1, batch=True, run_id=parent_run)
    assert first.run_id != worker.log.run_id
    if legacy:
        saved = copy.deepcopy(ledger._load(worker.log))
        for row in saved["reservations"].values():
            row.pop("reservation_id")
        worker.log.save_json("manifests", "budget_ledger", saved)
    ledger.settle_usage(worker.log, first, {
        "id": "resp-recovered", "usage": {"input_tokens": 40, "output_tokens": 20},
    })
    if legacy:
        # Samotný request hash nesmí převzít settlement cizího runu.
        assert summary(worker)["output_tokens_reserved"] == 1000
        with pytest.raises(ContractError, match="max_output_tokens"):
            reservation(worker, 2, output=980, batch=True, run_id=parent_run)
    else:
        assert summary(worker)["output_tokens_reserved"] == 20
        reservation(worker, 2, output=980, batch=True, run_id=parent_run)
        assert summary(worker)["output_tokens_reserved"] == 1000
        with pytest.raises(ContractError, match="max_output_tokens"):
            reservation(worker, 3, output=1, batch=True, run_id=parent_run)


def test_missing_usage_does_not_zero_known_cost_and_unknown_price_stays_blocked(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_cost_microusd = 500
    worker.cfg.unknown_pricing = "block"
    first = reservation(worker, 1, cost=500)
    ledger.settle_usage(worker.log, first, {"id": "resp-no-usage"})
    assert summary(worker)["projected_cost_microusd_known"] == 500
    with pytest.raises(ContractError, match="max_cost_microusd"):
        reservation(worker, 2, cost=1)
    with pytest.raises(ContractError, match="unknown_pricing=block"):
        reservation(worker, 3)


def test_usage_above_reservation_blocks_more_work(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_output_tokens = 1000
    first = reservation(worker, 1)
    ledger.settle_usage(worker.log, first, {
        "id": "resp-overrun", "usage": {"input_tokens": 100, "output_tokens": 1001},
    })
    assert summary(worker)["output_tokens_reserved"] == 1001
    with pytest.raises(ContractError, match="max_output_tokens"):
        reservation(worker, 2, output=0)


@pytest.mark.parametrize("legacy", [False, True])
def test_settlement_recovers_after_json_write_failure(tmp_path, monkeypatch, legacy):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.max_output_tokens = 1000
    first = reservation(worker, 1)
    if legacy:
        saved = copy.deepcopy(ledger._load(worker.log))
        for row in saved["reservations"].values():
            row.pop("reservation_id")
        worker.log.save_json("manifests", "budget_ledger", saved)

    def interrupted(*args, **kwargs):
        raise OSError("Přerušený zápis JSON")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_save", interrupted)
        with pytest.raises(OSError, match="Přerušený"):
            ledger.settle_usage(worker.log, first, {
                "id": "resp-first", "usage": {"input_tokens": 40, "output_tokens": 20},
            })
    assert summary(worker)["output_tokens_reserved"] == 1000
    reservation(worker, 2, output=980)
    assert summary(worker)["output_tokens_reserved"] == 1000
    with pytest.raises(ContractError, match="max_output_tokens"):
        reservation(worker, 3, output=1)
