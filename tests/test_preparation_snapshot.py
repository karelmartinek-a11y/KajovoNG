"""Integrita přípravy, její obnova a bezpečné ukládání změn."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.delivery_preparation import prepare_delivery, validate_preparation_snapshot
from kajovo.core.generate_batch import digest
from delivery_fixtures import delivery_payloads
from test_workflows import make_worker, response


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
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.maximum_quality = True
    checkpoint = snapshot(quality=True, stage="A2")
    checkpoint["prompt_hash"] = digest(worker.cfg.prompt)
    checkpoint.pop("snapshot_hash")
    checkpoint["snapshot_hash"] = digest(checkpoint)
    worker.cfg.preparation_snapshot = deepcopy(checkpoint)
    fixed = deepcopy(checkpoint["structure"])
    fixed["files"][0]["behavior"] = "Úplné chování po kontrole."
    client = Mock()
    client.create_response.return_value = response(7, fixed)
    _, structure, _ = prepare_delivery(worker, client, "GENERATE", None, "test", [], [], None)
    assert client.create_response.call_count == 1
    assert structure == fixed
    assert worker.cfg.preparation_snapshot["canonical_stage"] == "A2Q"
    assert checkpoint["structure"] != fixed
    validate_preparation_snapshot(worker.cfg.preparation_snapshot, "GENERATE", True)


def test_completed_checkpoint_does_not_generate_again(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    checkpoint = snapshot()
    checkpoint["prompt_hash"] = digest(worker.cfg.prompt)
    checkpoint.pop("snapshot_hash")
    checkpoint["snapshot_hash"] = digest(checkpoint)
    worker.cfg.preparation_snapshot = checkpoint
    client = Mock()
    plan, structure, rid = prepare_delivery(worker, client, "GENERATE", None, "test", [], [], None)
    client.create_response.assert_not_called()
    assert (plan, structure, rid) == (checkpoint["plan"], checkpoint["structure"], "resp_checkpoint")


def test_modify_dry_run_keeps_out_unchanged(tmp_path):
    worker = make_worker(tmp_path, "MODIFY")
    worker.settings.dry_run_modify = True
    target = tmp_path / "out"
    target.mkdir(exist_ok=True)
    original = target / "hello.txt"
    original.write_text("původní", encoding="utf-8")
    saved = worker._save_out_files([{"path": "hello.txt", "content": "nový"}])
    assert saved == {"saved": [], "dry_run": True}
    assert original.read_text(encoding="utf-8") == "původní"


def test_modify_write_rejects_user_edit_during_generation(tmp_path):
    worker = make_worker(tmp_path, "MODIFY")
    target = tmp_path / "out"
    target.mkdir(exist_ok=True)
    original = target / "hello.txt"
    worker._delivery_overwrite_hashes = {}
    original.write_text("uživatelská změna", encoding="utf-8")
    with pytest.raises(ContractError, match="OUT se během generování změnil"):
        worker._save_out_files([{"path": "hello.txt", "content": "výsledek"}])
    assert original.read_text(encoding="utf-8") == "uživatelská změna"
