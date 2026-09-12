from __future__ import annotations

import json
from pathlib import Path

from kajovo.core.batch_submit import exact_batch_matches
from kajovo.core.batch_completion import recover_unknown_submission
from kajovo.core.runlog import RunLogger, verified_output_evidence


def test_exact_batch_recovery_uses_input_file_id_and_endpoint(tmp_path):
    run = tmp_path / "RUN_010120260000_abcd"
    run.mkdir()
    state = {
        "run_id": run.name, "project": "P", "status": "failed",
        "submission_unknown": True, "submission_input_file_id": "file_work",
        "submission_endpoint": "/v1/responses", "out_dir": str(tmp_path / "out"),
    }
    (run / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    records = [
        {"id": "batch_other", "input_file_id": "file_other", "endpoint": "/v1/responses"},
        {"id": "batch_work", "input_file_id": "file_work", "endpoint": "/v1/responses", "status": "in_progress"},
    ]
    assert exact_batch_matches(records, "file_work") == [records[1]]
    recovered = recover_unknown_submission(str(run), records)
    assert recovered["id"] == "batch_work"
    current = json.loads((run / "run_state.json").read_text(encoding="utf-8"))
    assert current["submission_unknown"] is False
    assert current["batch_id"] == "batch_work"
    assert current["status"] == "batch_pending"


def test_unknown_batch_recovery_refuses_ambiguous_match(tmp_path):
    from kajovo.core.contracts import ContractError
    run = tmp_path / "RUN_010120260000_abcd"
    run.mkdir()
    (run / "run_state.json").write_text(json.dumps({
        "run_id": run.name, "project": "P", "submission_unknown": True,
        "submission_input_file_id": "same", "submission_endpoint": "/v1/responses"
    }), encoding="utf-8")
    records = [
        {"id": "a", "input_file_id": "same", "endpoint": "/v1/responses"},
        {"id": "b", "input_file_id": "same", "endpoint": "/v1/responses"},
    ]
    try:
        recover_unknown_submission(str(run), records)
    except ContractError:
        pass
    else:
        raise AssertionError("ambiguous work submission must be a blocker")


def test_rerun_reads_real_hashed_runlogger_manifest_and_verifies_hash(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    target = out / "a.txt"
    target.write_text("ok", encoding="utf-8")
    logger = RunLogger(str(tmp_path / "LOG"), "RUN_010120260000_abcd", "Project")
    logger.update_state({"out_dir": str(out)})
    import hashlib
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    path = logger.save_json("manifests", "out_saved_map", {
        "out_dir": str(out), "saved": [{"path": "a.txt", "sha256": digest}]
    })
    assert "out_saved_map_" in Path(path).name
    assert [e["path"] for e in verified_output_evidence(logger.paths.run_dir, str(out))] == ["a.txt"]
    target.write_text("changed", encoding="utf-8")
    assert verified_output_evidence(logger.paths.run_dir, str(out)) == []


def test_modify_batch_contract_does_not_require_in_source():
    source = Path("kajovo/desktop/application.py").read_text(encoding="utf-8")
    assert 'mode == "MODIFY"\n            and not batch' in source
    assert 'MODIFY vyžaduje existující vstupní adresář IN.' in source


def test_batch_submit_instruction_remains_manual_refresh():
    source = Path("kajovo/desktop/application.py").read_text(encoding="utf-8")
    assert "Obnovit stav; tím zahájíte periodické sledování" in source
    assert "start_monitoring(" not in source
