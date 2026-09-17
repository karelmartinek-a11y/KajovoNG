from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from kajovo.core.batch_completion import recover_unknown_submission
from kajovo.core.batch_submit import exact_batch_matches
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.progress import ProgressClock, ProgressEvent
from kajovo.core.runlog import RunLogger, verified_output_evidence
from kajovo.core.runs.executor import RunExecutor as RunWorker


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


def test_prevalidated_work_batch_performs_only_the_single_work_post():
    client = OpenAIClient("test", base_url="https://example.invalid/v1")
    client._policy = Mock()
    client._policy.proofs = {"proof": {"state": "verified"}}
    client._policy.key.return_value = "proof"
    client.file_content = Mock(side_effect=AssertionError("work submit must not fetch or preflight the JSONL again"))
    client._req = Mock(return_value={"id": "batch_work", "status": "validating"})
    rows = [{
        "custom_id": "row-1", "method": "POST", "url": "/v1/responses",
        "body": {"model": "gpt-4o-mini", "text": {"format": {"type": "text"}}},
    }]

    result = client.create_batch("file_work", _prevalidated_rows=rows)

    assert result["id"] == "batch_work"
    client.file_content.assert_not_called()
    client._req.assert_called_once_with(
        "POST", "/batches",
        json_body={"input_file_id": "file_work", "endpoint": "/v1/responses", "completion_window": "24h"},
    )
    assert client._policy.mock_calls == []
    assert client._policy.proofs == {"proof": {"state": "verified"}}


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


def test_partial_run_is_a_real_terminal_progress_state():
    clock = ProgressClock(now=10.0)
    event = ProgressEvent("RUN", "partial", detail="Částečný výstup", timestamp=12.5)
    clock.update(event)
    assert clock.state == "partial"
    assert clock.finished == 12.5
    elapsed, _age, eta = clock.times(now=20.0)
    assert elapsed == 2.5
    assert eta is None


def test_missing_deliverables_report_records_reason(tmp_path):
    worker = RunWorker.__new__(RunWorker)
    worker.cfg = SimpleNamespace(out_dir=str(tmp_path))
    worker._log_debug = lambda _message: None
    report = worker._write_missing_files_report([
        {"path": "assets/logo.png", "purpose": "logo", "reason": "binární výstup"}
    ])
    assert report is not None
    text = Path(report).read_text(encoding="utf-8")
    assert "assets/logo.png" in text
    assert "binární výstup" in text
    assert "automaticky nedodává" in text


def test_user_progress_no_longer_exposes_obsolete_english_stage_messages():
    source = Path("kajovo/core/runs/executor.py").read_text(encoding="utf-8")
    batch_source = Path("kajovo/core/runs/batch_execution.py").read_text(encoding="utf-8")
    obsolete = (
        "C: building batch JSONL...",
        "IN mirror: scan + manifest + upload...",
        "A1: PLAN request...",
        "A2: STRUCTURE request...",
        "QFILE: request...",
    )
    assert not any(message in source for message in obsolete)
    assert 'stage="Lokální validace"' in source
    assert 'stage="Příprava BATCH"' in batch_source


def test_modify_requires_existing_input_in_studio(qtbot, tmp_path):
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.workbench import Workbench

    context = StudioContext(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        Operations(None),
        api_key="test-key",
    )
    context.models = ["gpt-4.1"]
    workbench = Workbench(context)
    qtbot.addWidget(workbench)
    workbench.widgets["project"].setText("test")
    workbench.prompt.setPlainText("Uprav projekt.")
    workbench.widgets["mode"].setCurrentIndex(workbench.widgets["mode"].findData("MODIFY"))
    workbench.widgets["model"].setCurrentIndex(workbench.widgets["model"].findData("gpt-4.1"))
    workbench.widgets["out_dir"].setText(str(tmp_path / "out"))
    workbench.widgets["in_dir"].clear()
    assert not workbench.validate()
    assert "vstupní adresář" in workbench.validation.text()


def test_batch_monitoring_starts_only_after_explicit_refresh():
    source = Path("kajovo/studio/batches.py").read_text(encoding="utf-8")
    assert "self.timer.timeout.connect(lambda: self.refresh(automatic=True))" in source
    assert "if not automatic:" in source
    assert "self.poll_started = time.monotonic()" in source


def test_live_generate_partial_semantics_are_wired_end_to_end():
    pipeline = Path("kajovo/core/runs/generate.py").read_text(encoding="utf-8")
    from kajovo.studio.operations import STATES

    assert '"status": "partial" if missing_deliverables else "files_complete_unverified"' in pipeline
    executor = Path("kajovo/core/runs/executor.py").read_text(encoding="utf-8")
    assert 'final_status in ("completed", "partial", "dry_run", "files_complete_unverified")' in executor
    assert STATES["partial"] == "Dokončeno s chybami"
