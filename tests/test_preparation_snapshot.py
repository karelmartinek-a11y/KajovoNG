"""Integrita přípravy, její obnova a bezpečné ukládání změn."""

import hashlib
from copy import deepcopy
from unittest.mock import Mock

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.delivery_preparation import prepare_delivery, validate_preparation_snapshot
from kajovo.core.generate_batch import digest
from kajovo.core.orchestration.contracts import canonical_sha256
from kajovo.core.orchestration.preparation import validate_graph
from change_v2_fixtures import format_names, run, scenario
from delivery_fixtures import delivery_payloads
from test_workflows import make_worker


def snapshot(mode="GENERATE", quality=False, stage=None):
    req, plan, struct = delivery_payloads(mode)
    prefix = "A" if mode == "GENERATE" else "B"
    value = {"version": 1, "mode": mode, "maximum_quality": quality,
             "prompt_hash": digest("test"), "requirements": req, "plan": plan,
             "structure": struct, "canonical_stage": stage or prefix + ("2Q" if quality else "2"),
             "response_id": "resp_checkpoint"}
    value["snapshot_hash"] = digest(value)
    return value


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("quality", [False, True])
def test_snapshot_validates_and_returns_independent_copy(mode, quality):
    value = snapshot(mode, quality)
    restored = validate_preparation_snapshot(value, mode, quality)
    assert restored == value
    restored["plan"]["architecture_items"].clear()
    assert value["plan"]["architecture_items"]


@pytest.mark.parametrize("fault", ["content", "mode", "quality", "schema"])
def test_snapshot_rejects_tampering_or_wrong_run(fault):
    value = snapshot()
    mode, quality = "GENERATE", False
    if fault == "content":
        value["structure"]["files"][0]["path"] = "changed.py"
    elif fault == "mode":
        mode = "MODIFY"
    elif fault == "quality":
        quality = True
    else:
        value["requirements"].pop("product_intent")
        value.pop("snapshot_hash")
        value["snapshot_hash"] = digest(value)
    with pytest.raises(ContractError):
        validate_preparation_snapshot(value, mode, quality)


def test_resume_after_a2_runs_only_gate_and_persists_canonical_output(tmp_path):
    worker, client, responder = scenario(tmp_path, "GENERATE", stop_after_plan=True)
    results, errors = run(worker, client)
    assert results and not errors
    checkpoint = deepcopy(worker.cfg.preparation_snapshot)
    worker, client, responder = scenario(
        tmp_path / "resume", "GENERATE", stop_after_plan=True, maximum_quality=True,
    )
    checkpoint["maximum_quality"] = True
    checkpoint.pop("snapshot_hash")
    checkpoint["snapshot_hash"] = canonical_sha256(checkpoint)
    worker.cfg.preparation_snapshot = deepcopy(checkpoint)
    fixed = deepcopy(checkpoint["graph"])
    fixed["file_specs"][0]["spec"]["behavior"] = "Úplné chování po kontrole."

    def correct_gate(name, value, data):
        assert name == "A2Q_QUALITY_GATE_V2"
        assert data["implementation_graph"] == checkpoint["graph"]
        value["result"]["data"]["corrected_file_specs"] = deepcopy(fixed["file_specs"])
        return value

    responder.mutate = correct_gate
    responder.calls.clear()
    client.create_response.reset_mock()
    results, errors = run(worker, client)
    assert results and not errors
    structure = results[0]["structure"]
    assert client.create_response.call_count == 1
    assert structure == fixed
    assert worker.cfg.preparation_snapshot["canonical_stage"] == "A2Q"
    assert format_names(responder) == ["A2Q_QUALITY_GATE_V2"]
    assert checkpoint["graph"] != fixed
    validate_graph(worker, worker.cfg.preparation_snapshot["graph"])
    stored = worker.cfg.preparation_snapshot
    assert stored["snapshot_hash"] == canonical_sha256(
        {key: value for key, value in stored.items() if key != "snapshot_hash"}
    )


def test_completed_checkpoint_does_not_generate_again(tmp_path):
    worker, initial_client, _ = scenario(tmp_path, "GENERATE", stop_after_plan=True)
    results, errors = run(worker, initial_client)
    assert results and not errors
    checkpoint = deepcopy(worker.cfg.preparation_snapshot)
    client = Mock()
    plan, structure, rid = prepare_delivery(worker, client, "GENERATE", None, "test", [], [], None)
    client.create_response.assert_not_called()
    assert (plan, structure, rid) == (checkpoint["plan"], checkpoint["graph"], checkpoint["response_id"])
    assert worker.cfg.preparation_snapshot == checkpoint


def test_modify_dry_run_keeps_out_unchanged(tmp_path):
    worker = make_worker(tmp_path, "MODIFY")
    worker.cfg.dry_run = True
    target = tmp_path / "out"
    target.mkdir(exist_ok=True)
    original = target / "hello.txt"
    original.write_text("původní", encoding="utf-8")
    saved = worker._save_out_files([{"path": "hello.txt", "content": "nový"}])
    assert saved["saved"] == []
    assert saved["dry_run"] is True
    assert saved["published"] is False
    assert saved["publication_state"] == "blocked_dry_run"
    assert [row["path"] for row in saved["staged"]] == ["hello.txt"]
    assert original.read_text(encoding="utf-8") == "původní"


def test_modify_write_rejects_user_edit_during_generation(tmp_path):
    worker = make_worker(tmp_path, "MODIFY")
    target = tmp_path / "out"
    target.mkdir(exist_ok=True)
    original = target / "hello.txt"
    original.write_text("původní stav", encoding="utf-8")
    worker._delivery_overwrite_hashes = {
        "hello.txt": hashlib.sha256(original.read_bytes()).hexdigest()
    }
    original.write_text("uživatelská změna", encoding="utf-8")
    with pytest.raises(ContractError, match="OUT se během generování změnil"):
        worker._save_out_files([{"path": "hello.txt", "content": "výsledek"}])
    assert original.read_text(encoding="utf-8") == "uživatelská změna"
