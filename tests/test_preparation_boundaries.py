"""Regrese vadných plánů, směrování oprav a pravdivé evidence fází."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from delivery_fixtures import delivery_payloads
from kajovo.core.contracts import ContractError, RemoteResponseError
from kajovo.core.delivery_preparation import prepare_delivery, validate_preparation_snapshot
from kajovo.core.generate_batch import digest
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.requirements import validate_plan
from kajovo.core.user_errors import describe_error
from test_delivery_pipeline import _client
from test_workflows import make_worker


def run_preparation(tmp_path, mode, values):
    worker = make_worker(tmp_path, mode)
    worker.cfg.maximum_quality = False
    client, calls = _client(values)
    return worker, client, calls


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_plan_is_repaired_before_structure_and_preserves_attempts(tmp_path, mode):
    req, plan, struct = delivery_payloads(mode)
    bad = deepcopy(plan)
    bad["architecture_items"][0]["requirement_ids"].append("AC-01")
    worker, client, calls = run_preparation(tmp_path, mode, [req, bad, plan, struct])
    with patch.object(worker, "_set"):
        prepare_delivery(worker, client, mode, None, "zadání", [], [], None)
    prefix = "A" if mode == "GENERATE" else "B"
    assert [c["text"]["format"]["name"] for c in calls][:3] == [prefix + "0R_REQUIREMENTS", prefix + "1_PLAN", prefix + "1_PLAN"]
    repair = json.loads("".join(c["text"] for m in calls[2]["input"] for c in m["content"]))
    assert repair["plan"] == bad
    assert repair["requirements"] == req
    assert repair["validation_issues"][0]["stage"] == prefix + "1"
    adapter = LegacyRunAdapter(worker.log.paths.run_dir)
    records = adapter.validations()
    assert [r["status"] for r in records].count("failed") == 1
    assert all(s["status"] == "completed" for s in worker.log.bundle.steps())
    assert len([r for r in adapter.requests() if r["retry_attempt"] == 1]) == 1


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_identical_invalid_plan_stops_without_structure(tmp_path, mode):
    req, plan, _ = delivery_payloads(mode)
    plan["architecture_items"][0]["responsibility"] = ""
    worker, client, calls = run_preparation(tmp_path, mode, [req, plan, plan])
    with patch.object(worker, "_set"), pytest.raises(ContractError):
        prepare_delivery(worker, client, mode, None, "zadání", [], [], None)
    assert len(calls) == 3
    assert worker.cfg.preparation_snapshot["canonical_stage"].endswith("0R")
    assert worker.log.bundle.steps()[-1]["status"] == "failed"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_invalid_plan_checkpoint_is_rejected_before_network(tmp_path, mode):
    req, plan, _ = delivery_payloads(mode)
    plan["architecture_items"][0]["requirement_ids"].append("INV-1")
    snap = {"version": 1, "mode": mode, "maximum_quality": False,
            "requirements": req, "plan": plan, "structure": None,
            "canonical_stage": ("A" if mode == "GENERATE" else "B") + "1", "response_id": "resp_plan"}
    snap["snapshot_hash"] = digest(snap)
    with pytest.raises(ContractError):
        validate_preparation_snapshot(snap, mode, False)


def test_plan_reports_all_independent_issues():
    req, plan, _ = delivery_payloads("GENERATE")
    plan["architecture_items"][0].update(requirement_ids=["AC-1"], responsibility="")
    with pytest.raises(ContractError) as caught:
        validate_plan(req, plan, "GENERATE")
    assert {i.code for i in caught.value.issues} == {
        "architecture_requirement_invalid", "architecture_responsibility_empty", "plan_coverage_missing"}
    assert describe_error(caught.value).cause_known


def test_received_response_does_not_complete_unvalidated_step(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    bundle = worker.log.bundle
    step = bundle.ensure_step("A1")
    bundle.update_step(step["step_id"], validation_required=True)
    bundle.record_response({"id": "resp", "status": "completed"}, step_id=step["step_id"])
    assert bundle.steps()[0]["status"] == "validating_result"
    assert not bundle.steps()[0]["finished_at"]


@pytest.mark.parametrize("code", ["credit_balance_exhausted", "context_length_exceeded", "max_output_tokens"])
def test_remote_cause_survives_runtime_and_persistence(tmp_path, code):
    worker = make_worker(tmp_path, "GENERATE")
    response = {"id": "resp_failure", "status": "failed", "error": {"code": code}, "_request_id": "req_provider"}
    error = RemoteResponseError(response)
    worker.log.exception("run", error)
    state = json.loads(Path(worker.log.state_path).read_text("utf-8"))
    assert state["failure_detail"]["code"] == code
    assert state["failure_detail"]["cause_known"]
    assert state["failure_detail"]["evidence"]["response_id"] == "resp_failure"


def test_schema_invalid_reply_is_repaired_in_same_phase(tmp_path):
    req, plan, struct = delivery_payloads("GENERATE")
    worker, client, calls = run_preparation(tmp_path, "GENERATE", [{}, req, plan, struct])
    with patch.object(worker, "_set"):
        prepare_delivery(worker, client, "GENERATE", None, "zadání", [], [], None)
    assert len(calls) == 4
    assert calls[0]["text"] == calls[1]["text"]
    assert worker.log.bundle.steps()[0]["status"] == "completed"


def test_current_output_check_is_read_only_and_detects_changes(tmp_path):
    import hashlib
    from kajovo.core.runlog import inspect_current_outputs
    worker = make_worker(tmp_path, "GENERATE")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "same.txt").write_text("ok", encoding="utf-8")
    (out / "changed.txt").write_text("changed", encoding="utf-8")
    worker.log.update_state({"out_dir": str(out), "generated_hashes": {
        path: hashlib.sha256(b"ok").hexdigest() for path in ("same.txt", "changed.txt", "missing.txt")}})
    before = Path(worker.log.state_path).read_bytes()
    rows = inspect_current_outputs(worker.log.paths.run_dir)
    assert {r["path"]: r["current_status"] for r in rows} == {
        "same.txt": "matching", "changed.txt": "changed", "missing.txt": "missing"}
    assert Path(worker.log.state_path).read_bytes() == before


def test_generate_keeps_foreign_output(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    out = Path(worker.cfg.out_dir)
    out.mkdir(exist_ok=True)
    target = out / "main.py"
    target.write_text("uživatelská práce", encoding="utf-8")
    with pytest.raises(ContractError, match="zachován"):
        worker._save_out_files([{"path": "main.py", "content": "jiný obsah"}])
    assert target.read_text("utf-8") == "uživatelská práce"


def test_modify_plan_paths_are_checked_before_structure(tmp_path):
    req, plan, _ = delivery_payloads("MODIFY")
    plan["change_plan"]["files_to_add"] = [{"path": "../outside.py", "intent": "změna"}]
    worker, client, calls = run_preparation(tmp_path, "MODIFY", [req, plan, plan])
    with patch.object(worker, "_set"), pytest.raises(ContractError) as caught:
        prepare_delivery(worker, client, "MODIFY", None, "zadání", [], [], None)
    assert caught.value.issues[0].stage == "B1"
    assert len(calls) == 3
    assert worker.cfg.preparation_snapshot["canonical_stage"] == "B0R"


@pytest.mark.parametrize("status", ["stopped", "cancelled", "response_pending", "submission_unknown"])
def test_interruption_preserves_truthful_step_status(tmp_path, status):
    worker = make_worker(tmp_path, "GENERATE")
    worker.log.begin_validated_step("A1", kind="preparation")
    worker.log.update_state({"status": status})
    step = worker.log.bundle.steps()[0]
    assert step["status"] == status
    assert bool(step["finished_at"]) == (status in {"stopped", "cancelled"})
