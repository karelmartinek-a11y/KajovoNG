"""Regrese vadných plánů, směrování oprav a pravdivé evidence fází."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from delivery_fixtures import delivery_payloads
from kajovo.core.contracts import ContractError, RemoteResponseError
from kajovo.core.delivery_preparation import validate_preparation_snapshot
from kajovo.core.generate_batch import digest
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.requirements import validate_plan
from kajovo.core.user_errors import describe_error
from preparation_v2_helpers import decoded, preparation_scenario, prepare
from test_workflows import make_worker


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_plan_is_repaired_before_structure_and_preserves_attempts(tmp_path, mode):
    attempts = []
    invalid = []

    def mutate(name, value, context):
        if name.endswith("1_PLAN_V2"):
            attempts.append(deepcopy(value))
            if len(attempts) == 1:
                plan = value["result"]["data"]
                if mode == "MODIFY":
                    plan = plan["plan"]
                plan["components"][0]["requirement_ids"].append("AC-01")
                invalid.append(deepcopy(value["result"]["data"]))
        return value

    worker, client, responder = preparation_scenario(tmp_path, mode, mutate=mutate)
    prepare(worker, client)
    calls = responder.calls
    prefix = "A" if mode == "GENERATE" else "B"
    assert [c["text"]["format"]["name"] for c in calls][:3] == [
        prefix + "0R_REQUIREMENTS_V2", prefix + "1_PLAN_V2", prefix + "1_PLAN_V2"]
    repair = decoded(calls[2])
    assert repair["input"] == decoded(calls[1])
    assert "neznámý requirement" in repair["repair"]["error"]
    assert len(attempts) == 2
    adapter = LegacyRunAdapter(worker.log.paths.run_dir)
    records = adapter.validations()
    assert [r["status"] for r in records].count("failed") == 1
    assert all(s["status"] == "completed" for s in worker.log.bundle.steps())
    evidence = {
        "repair_requests": len([r for r in adapter.requests() if r["retry_attempt"] == 1]),
        "invalid_candidate": invalid[0] in repair["repair"].values(),
    }
    assert evidence == {"repair_requests": 1, "invalid_candidate": True}


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("fault", ["empty_responsibility", "unknown_requirement"])
def test_identical_invalid_plan_stops_without_structure(tmp_path, mode, fault):
    def mutate(name, value, context):
        if name.endswith("1_PLAN_V2"):
            plan = value["result"]["data"]
            if mode == "MODIFY":
                plan = plan["plan"]
            if fault == "empty_responsibility":
                plan["components"][0]["responsibility"] = ""
            else:
                plan["components"][0]["requirement_ids"] = ["UNKNOWN"]
        return value

    worker, client, responder = preparation_scenario(tmp_path, mode, mutate=mutate)
    with patch.object(worker, "_set"), pytest.raises(ContractError):
        prepare(worker, client)
    assert len(responder.calls) == 3
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
    def mutate(name, value, context):
        return {} if len(responder.calls) == 1 else value

    worker, client, responder = preparation_scenario(tmp_path, "GENERATE", mutate=mutate)
    prepare(worker, client)
    calls = responder.calls
    assert len(calls) == 5
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
    def mutate(name, value, context):
        if name == "B1_PLAN_V2":
            value["result"]["data"]["files_to_add"] = ["../outside.py"]
        return value

    worker, client, responder = preparation_scenario(tmp_path, "MODIFY", mutate=mutate)
    with patch.object(worker, "_set"), pytest.raises(ContractError) as caught:
        prepare(worker, client)
    assert "outside.py" in str(caught.value)
    assert all("SPINE" not in p["text"]["format"]["name"] for p in responder.calls)
    assert worker.cfg.preparation_snapshot["canonical_stage"] == "B0R"


@pytest.mark.parametrize("status", ["stopped", "cancelled", "response_pending", "submission_unknown"])
def test_interruption_preserves_truthful_step_status(tmp_path, status):
    worker = make_worker(tmp_path, "GENERATE")
    worker.log.begin_validated_step("A1", kind="preparation")
    worker.log.update_state({"status": status})
    step = worker.log.bundle.steps()[0]
    assert step["status"] == status
    assert bool(step["finished_at"]) == (status in {"stopped", "cancelled"})
