"""Offline obnova kanonické přípravy z evidence konkrétního běhu."""

import json

import pytest

from kajovo.core.generate_batch import digest
from kajovo.core.contracts import ContractError
from kajovo.core.recovery import recover_run
from test_requirements import _documents


def snapshot(mode="GENERATE", quality=True):
    prefix = "A" if mode == "GENERATE" else "B"
    requirements, plan, structure = _documents(mode)
    value = dict(requirements=requirements, plan=plan, structure=structure,
                 mode=mode, prompt_hash=digest(""), canonical_stage=prefix + ("2Q" if quality else "2"),
                 response_id="resp_canonical", maximum_quality=quality, version=1)
    value["snapshot_hash"] = digest(value)
    return value


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("quality", [False, True])
def test_recovery_uses_only_run_state_snapshot(tmp_path, mode, quality):
    run = tmp_path / "run"
    (run / "requests").mkdir(parents=True)
    saved = snapshot(mode, quality)
    ui = {"mode": mode, "maximum_quality": quality, "preparation_snapshot": snapshot()}
    (run / "run_state.json").write_text(json.dumps({
        "ui_state": ui, "preparation_snapshot": saved, "last_response_id": "resp_file",
    }), encoding="utf-8")
    (run / "requests" / "request.json").write_text(json.dumps({"ui_state": ui}), encoding="utf-8")
    restored, previous, files = recover_run(tmp_path, "run")
    assert restored["preparation_snapshot"] == saved
    assert restored["maximum_quality"] is quality
    assert previous == "resp_canonical"
    assert files == saved["structure"]["touched_files" if mode == "MODIFY" else "files"]


@pytest.mark.parametrize("missing", [True, False])
def test_unprepared_new_run_does_not_borrow_snapshot(tmp_path, missing):
    run = tmp_path / "run"
    run.mkdir()
    state = {"ui_state": {"mode": "GENERATE", "maximum_quality": True,
                          "preparation_snapshot": snapshot()}, "last_response_id": "resp_old",
             "out_dir": str(tmp_path / "OUT")}
    other = tmp_path / "other"
    (other / "responses").mkdir(parents=True)
    (other / "run_state.json").write_text(json.dumps({
        "out_dir": state["out_dir"], "preparation_snapshot": snapshot(),
    }), encoding="utf-8")
    (other / "responses" / "A2_response.json").write_text(json.dumps({
        "id": "resp_foreign", "output_text": json.dumps({
            "contract": "A2_STRUCTURE", "files": [{"path": "foreign.py"}],
        }),
    }), encoding="utf-8")
    if not missing:
        state["preparation_snapshot"] = None
    (run / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    restored, previous, files = recover_run(tmp_path, "run")
    assert restored["preparation_snapshot"] is None
    assert previous is None and files == []


def test_recovery_rejects_changed_snapshot(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    saved = snapshot()
    saved["structure"]["files"] = [{"path": "changed.py"}]
    (run / "run_state.json").write_text(json.dumps({
        "ui_state": {"mode": "GENERATE"}, "preparation_snapshot": saved,
    }), encoding="utf-8")
    with pytest.raises(ContractError, match="změněn|poškozen"):
        recover_run(tmp_path, "run")


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("stage", ["0R", "1"])
def test_recovery_preserves_partial_preparation(tmp_path, mode, stage):
    run = tmp_path / "run"
    run.mkdir()
    saved = snapshot(mode)
    saved["canonical_stage"] = ("A" if mode == "GENERATE" else "B") + stage
    saved["structure"] = None
    if stage == "0R":
        saved["plan"] = None
    saved.pop("snapshot_hash")
    saved["snapshot_hash"] = digest(saved)
    (run / "run_state.json").write_text(json.dumps({
        "ui_state": {"mode": mode, "maximum_quality": True}, "preparation_snapshot": saved,
    }), encoding="utf-8")
    restored, previous, files = recover_run(tmp_path, "run")
    assert restored["preparation_snapshot"] == saved
    assert previous == saved["response_id"] and files == []


@pytest.mark.parametrize("mode", ["QA", "QFILE"])
def test_other_modes_keep_response_continuation(tmp_path, mode):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_state.json").write_text(json.dumps({
        "ui_state": {"mode": mode, "maximum_quality": False, "preparation_snapshot": None},
        "last_response_id": "resp_answer",
    }), encoding="utf-8")
    assert recover_run(tmp_path, "run")[1] == "resp_answer"
