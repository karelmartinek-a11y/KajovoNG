"""Uzavřené lokální kontrakty a bezeztrátový převod Unicode."""
import json
from dataclasses import asdict

import pytest

from kajovo.core.cascade_types import (
    CascadeDefinition, CascadeStep, CascadeInput, CascadeOutput,
    CascadeOutputRef, CascadeDecisionOption,
)
from kajovo.core.cascade_contract import step_signature
from kajovo.core.photo_templates import PhotoTemplateStore
from kajovo.core.orchestration.authorization import automatic_attempt_limit
from utf8nobom.app import normalize_text_bytes


@pytest.mark.parametrize("cls", [CascadeStep, CascadeInput, CascadeOutput, CascadeOutputRef, CascadeDecisionOption])
def test_cascade_rejects_unknown_fields(cls):
    with pytest.raises(ValueError, match="neznámá pole"):
        cls.from_dict({"typo": True})


@pytest.mark.parametrize("version", [True, 0, 3, 99, "2"])
def test_cascade_rejects_unsupported_versions(version):
    with pytest.raises(ValueError):
        CascadeDefinition.from_dict({"name": "Test", "version": version})


def test_signature_includes_explicit_conversation_expression():
    step = CascadeStep()
    before = step_signature(step)
    step.previous_response_id_expr = "response_id"
    assert step_signature(step) != before


@pytest.mark.parametrize("field,value", [("template_id", []), ("name", 3), ("builtin", 0),
                                       ("template_id", "builtin-reserved"), ("created_at", "invalid")])
def test_photo_template_load_rejects_invalid_types(tmp_path, field, value):
    store = PhotoTemplateStore(tmp_path / "templates.json")
    row = asdict(store.create("Name", "Prompt"))
    row[field] = value
    store.path.write_text(json.dumps({"schema_version": 1, "templates": [row]}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.list()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
def test_encoding_conversion_keeps_content_feff(encoding):
    text = "\ufeffobsah\r\n"
    result, changed = normalize_text_bytes(text.encode(encoding))
    assert changed
    assert result == text.encode("utf-8")


@pytest.mark.parametrize("policy,expected", [("off", 1), ("within_approval", 3), (None, 1)])
def test_automatic_repair_requires_policy(policy, expected):
    assert automatic_attempt_limit({"auto_repair": policy}) == expected


def test_incomplete_run_uses_time_not_name_and_has_no_thirty_run_limit(tmp_path):
    from kajovo.core.runlog import find_last_incomplete_run
    for number in range(35):
        root = tmp_path / f"RUN_Z{number:03}"
        root.mkdir()
        (root / "run_state.json").write_text(json.dumps({"status": "completed", "created_at": number}), encoding="utf-8")
    for name, stamp in (("RUN_A_old", 100), ("RUN_A_new", 200)):
        root = tmp_path / name
        root.mkdir()
        (root / "run_state.json").write_text(json.dumps({"status": "running", "created_at": stamp}), encoding="utf-8")
    broken = tmp_path / "RUN_Zbroken"
    broken.mkdir()
    (broken / "run_state.json").write_text("[]", encoding="utf-8")
    assert find_last_incomplete_run(str(tmp_path)) == "RUN_A_new"


def test_request_evidence_does_not_claim_prepared_payload_was_sent(tmp_path):
    from kajovo.core.runlog import RunLogger
    from kajovo.core.run_bundle import LegacyRunAdapter
    logger = RunLogger(str(tmp_path), "RUN_evidence", "test")
    record = logger.bundle.record_request({"input": "data"})
    identifier = record["request_record_id"]
    assert record["sent_at"] is None and record["transmission_state"] == "prepared"
    logger.bundle.record_transport_event(identifier, "dispatch_started")
    logger.bundle.record_transport_event(identifier, "submission_unknown")
    latest = LegacyRunAdapter(logger.paths.run_dir).requests()[0]
    assert latest["sent_at"] is None
    assert latest["dispatch_started_at"]
    logger.bundle.record_transport_event(identifier, "response_received", status_code=400, remote_request_id="req_remote")
    latest = LegacyRunAdapter(logger.paths.run_dir).requests()[0]
    assert latest["sent_at"] and latest["remote_request_id"] == "req_remote"


def test_diagnostic_stage_never_marks_unvisited_production_complete():
    from kajovo.core.progress import ProgressEvent
    from kajovo.core.progress_display import build_steps
    events = [ProgressEvent("Diagnostika", "active")]
    steps = {row.key: row.state for row in build_steps(events, mode="GENERATE")}
    assert steps["Diagnostika"] == "current"
    assert steps["A3"] == steps["Ukládání"] == "pending"
    events.append(ProgressEvent("Diagnostika", "completed"))
    assert next(row.state for row in build_steps(events) if row.key == "Diagnostika") == "done"


def test_history_requires_same_batch_to_be_completed_and_unimported():
    from kajovo.studio.history_models import build_run
    state = {"batch_id": "old", "generate_batches": {"new": {}},
             "batch_imports": {"old": {"import_status": "files_complete_unverified"}},
             "batch_records": {"old": {"status": "completed"}, "new": {"status": "in_progress"}}}
    view = build_run({"run_id": "RUN", "status": "batch_pending", "mode": "GENERATE"}, state=state)
    assert view.status.key == "batch_pending"
    state["batch_records"]["new"]["status"] = "completed"
    view = build_run({"run_id": "RUN", "status": "batch_pending", "mode": "GENERATE"}, state=state)
    assert view.status.key == "ready_to_import"


def test_modern_history_rejects_corrupt_evidence_but_legacy_reader_stays_lenient(tmp_path):
    from pathlib import Path
    from kajovo.core.runlog import RunLogger
    from kajovo.core.run_bundle import LegacyRunAdapter
    from kajovo.core.orchestration.errors import OrchestrationError
    logger = RunLogger(str(tmp_path), "RUN_modern", "test")
    Path(logger.paths.run_dir, "requests", "_record_bad.json").write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises((ValueError, OrchestrationError)):
        LegacyRunAdapter(logger.paths.run_dir).requests()
    legacy = tmp_path / "RUN_legacy" / "requests"
    legacy.mkdir(parents=True)
    (legacy / "bad.json").write_text('{"a":1,"a":2}', encoding="utf-8")
    assert LegacyRunAdapter(legacy.parent).requests()[0]["full_payload"] == {"a": 2}


@pytest.mark.parametrize("field,value", [("id", "foreign"), ("id", None),
                                        ("input_file_id", "foreign"), ("input_file_id", None),
                                        ("endpoint", "/v1/images/edits"), ("endpoint", None)])
def test_batch_identity_rejects_each_broken_link(field, value):
    from kajovo.core.batch_submit import validate_batch_identity
    from kajovo.core.contracts import ContractError
    payload = {"id": "batch_1", "input_file_id": "file_1", "endpoint": "/v1/responses"}
    payload[field] = value
    with pytest.raises(ContractError):
        validate_batch_identity(payload, batch_id="batch_1", input_file_id="file_1")


@pytest.mark.parametrize("fault", [None, "status_missing", "root_extra", "nested_extra", "field_missing"])
def test_legacy_bundle_uses_its_complete_closed_mask(tmp_path, fault):
    from kajovo.core.batch_completion import import_bundle
    value = {"contract": "C_FILES_ALL", "project": {key: "test" for key in ("name", "target_os", "runtime", "language")},
             "root": "", "files": [{"path": "result.txt", "purpose": "test", "content": "data"}],
             "build_run": {key: [] for key in ("prerequisites", "commands", "verification")}, "notes": []}
    if fault == "root_extra":
        value["unknown"] = True
    elif fault == "nested_extra":
        value["build_run"]["unknown"] = True
    elif fault == "field_missing":
        value["files"][0].pop("purpose")
    entry = {"custom_id": "C1", "response": {"status_code": 200,
                                               "body": {"status": "completed", "output_text": json.dumps(value)}}}
    if fault == "status_missing":
        entry["response"].pop("status_code")
    result = import_bundle((json.dumps(entry) + "\n").encode(), str(tmp_path))
    if fault is None:
        assert result["status"] == "files_complete_unverified"
        assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "data"
    else:
        assert result["status"] == "partial" and not result["written"]
        assert not (tmp_path / "result.txt").exists()


def test_ready_tasks_moves_past_completed_wave():
    from kajovo.core.orchestration.waves import ExecutionDag, ready_tasks
    dag = ExecutionDag((("provider",), ("consumer",)), {"provider": (), "consumer": ("provider",)}, {})
    assert ready_tasks(dag, {}) == ("provider",)
    assert ready_tasks(dag, {"provider": True}) == ("consumer",)
    assert ready_tasks(dag, {"provider": True, "consumer": True}) == ()


@pytest.mark.parametrize("blocked", [False, True])
def test_model_refill_keeps_unavailable_selection_and_signal_state(qapp, blocked):
    from PySide6.QtWidgets import QComboBox
    from kajovo.studio.model_selection import refill_models
    combo = QComboBox()
    combo.addItem("original", "unavailable")
    combo.blockSignals(blocked)
    refill_models(combo, ["available"], "available")
    assert combo.currentData() == "unavailable"
    assert combo.property("model_unavailable") is True
    assert combo.signalsBlocked() is blocked


def test_separate_operation_managers_share_all_reserved_roots(tmp_path, qapp):
    from kajovo.studio.operations import Operations
    first, second = Operations(None), Operations(None)
    token = "regression-reservation"
    first._output_reservations[token] = (tmp_path / "out", tmp_path / "backup")
    try:
        for path in (tmp_path, tmp_path / "out" / "nested", tmp_path / "backup"):
            with pytest.raises(ValueError, match="zapisuje"):
                second.assert_output_available(path)
        second.assert_output_available(tmp_path / "unrelated")
    finally:
        first._output_reservations.pop(token)
    second.assert_output_available(tmp_path / "out")
