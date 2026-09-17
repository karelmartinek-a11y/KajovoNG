from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QWidget

from kajovo.core.config import AppSettings
from kajovo.core.batch_completion import read_state, remember_remote_batch_state
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.runlog import RunLogger
from kajovo.studio.history_artifacts import ArtifactGuard
from kajovo.studio.history_details import classify_modify_files
from kajovo.studio.history_launcher import HistoryBranchLauncher
from kajovo.studio.history_models import RunTableModel, build_run
from kajovo.studio.history_policy import ActionAvailabilityPolicy
from kajovo.studio.history_state import present_state
from kajovo.studio.history_timeline import RunTrackView
from kajovo.studio.workbench import default_state


def _source(tmp_path, *, mode="QA", status="failed", batch=False):
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    ui = default_state(settings)
    ui.update(project="Projekt", prompt="Původní dotaz", mode=mode, model="gpt-4.1", out_dir=str(tmp_path / "OUT"))
    logger = RunLogger(settings.log_dir, "RUN_SOURCE", "Projekt")
    logger.update_state({"ui_state": ui})
    patch = {"status": status, "error": "Doložená chyba" if status in {"failed", "partial"} else ""}
    if batch:
        patch.update(batch_id="batch_existing", status="batch_pending")
    logger.update_state(patch)
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    return settings, ui, adapter


@pytest.mark.parametrize("key,label", [
    ("completed", "Dokončeno"), ("partial", "Částečně dokončeno"),
    ("failed", "Chyba"), ("cancelled", "Zrušeno"), ("running", "Běží"),
    ("response_pending", "Čeká na odpověď"), ("batch_pending", "BATCH běží"),
    ("files_complete_unverified", "Neověřeno"), ("dry_run", "Dry-run"),
    ("submission_unknown", "Neznámý výsledek"), ("blocked", "Blokováno"),
])
def test_central_state_mapping_has_text_icon_and_color(key, label):
    value = present_state(key)
    assert value.label == label
    assert value.symbol and value.color.startswith("#")


def test_remote_complete_with_missing_import_is_ready_to_import():
    value = present_state("completed", batch_import_pending=True)
    assert value.key == "ready_to_import"
    assert value.label == "K převzetí"


def test_remote_batch_state_is_stored_separately_from_local_import(tmp_path):
    settings, _ui, adapter = _source(tmp_path, batch=True)
    state = read_state(adapter.root)
    assert state["status"] == "batch_pending"
    assert remember_remote_batch_state(adapter.root, {"id": "batch_existing", "status": "completed"})
    state = read_state(adapter.root)
    assert state["status"] == "batch_pending"
    assert state["batch_records"]["batch_existing"]["status"] == "completed"
    assert "batch_imports" not in state


@pytest.mark.parametrize(
    "status,legacy,checkpoint,batch,rerun,repair,continue_,complete",
    [
        ("completed", False, True, False, True, False, False, False),
        ("partial", False, True, False, True, True, False, False),
        ("failed", False, True, False, True, True, False, False),
        ("running", False, True, False, True, False, True, False),
        ("failed", True, True, False, False, False, False, False),
        ("failed", False, False, False, False, False, False, False),
        ("failed", False, True, True, False, False, False, True),
    ],
)
def test_action_policy(status, legacy, checkpoint, batch, rerun, repair, continue_, complete):
    state = {"ui_state": {"mode": "QA"}}
    if batch:
        state["batch_id"] = "batch_1"
        state["batch_records"] = {"batch_1": {"status": "completed"}}
    checkpoints = [{"safe_to_continue": True, "_availability_valid": checkpoint, "checkpoint_type": "input_ready"}] if checkpoint else []
    decisions = ActionAvailabilityPolicy().evaluate(
        {"mode": "QA", "status": status}, state, checkpoints, legacy=legacy
    )
    assert decisions["rerun"].enabled is rerun
    assert decisions["repair"].enabled is repair
    assert decisions["continue"].enabled is continue_
    assert decisions["complete_batch"].enabled is complete
    assert decisions["edit_branch"].enabled is (rerun and not legacy and not batch)


def test_corrupt_checkpoint_and_unsupported_mode_disable_direct_actions():
    checkpoint = {"safe_to_continue": True, "_availability_valid": False}
    policy = ActionAvailabilityPolicy()
    corrupt = policy.evaluate({"mode": "QA", "status": "failed"}, {"ui_state": {"mode": "QA"}}, [checkpoint], legacy=False)
    comic = policy.evaluate({"mode": "COMIC", "status": "failed"}, {"ui_state": {"mode": "COMIC"}}, [checkpoint | {"_availability_valid": True}], legacy=False)
    assert not corrupt["repair"].enabled
    assert not comic["rerun"].enabled


def test_fully_imported_batch_allows_explicit_new_rerun_but_not_second_import():
    state = {
        "ui_state": {"mode": "QA"},
        "batch_id": "batch_1",
        "batch_records": {"batch_1": {"status": "completed"}},
        "batch_imports": {"batch_1": {"import_status": "files_complete_unverified"}},
    }
    decisions = ActionAvailabilityPolicy().evaluate(
        {"mode": "QA", "status": "completed"}, state,
        [{"safe_to_continue": True, "_availability_valid": True}], legacy=False,
    )
    assert decisions["rerun"].enabled
    assert not decisions["complete_batch"].enabled


def test_virtual_model_scales_without_per_segment_widgets(qtbot):
    runs = []
    step = {"step_id": "s", "stage": "QA", "title": "Odpověď", "status": "completed",
            "started_at": "2026-09-15T10:00:00+00:00", "finished_at": "2026-09-15T10:00:01+00:00"}
    for index in range(3000):
        runs.append(build_run({"run_id": f"RUN_{index}", "project": "P", "mode": "QA", "status": "completed"}, steps=[step | {"step_id": f"s{index}"}]))
    model = RunTableModel()
    model.set_runs(runs)
    view = RunTrackView()
    qtbot.addWidget(view)
    view.setModel(model)
    assert model.rowCount() == 3000
    assert len(view.findChildren(QWidget)) < 20


def test_browsing_filters_and_paid_operation_preview_are_offline(tmp_path, monkeypatch):
    monkeypatch.setattr("socket.create_connection", Mock(side_effect=AssertionError("network")))
    monkeypatch.setattr("requests.sessions.Session.request", Mock(side_effect=AssertionError("network")))
    settings, _ui, adapter = _source(tmp_path)
    context = SimpleNamespace(settings=settings, operations=Mock(), api_key="", models=[],
                              client=Mock(side_effect=AssertionError("OpenAI client")))
    preview = HistoryBranchLauncher(context).preview(
        adapter, adapter.checkpoints()[0]["checkpoint_id"], "rerun", "QA"
    )
    model = RunTableModel()
    model.set_runs([build_run(adapter.run_record(), steps=adapter.steps(), state=read_state(adapter.root))])
    model.apply_filters({"search": "RUN_SOURCE"})
    assert preview.first_paid_operation == "QA"
    assert model.rowCount() == 1
    context.client.assert_not_called()


@pytest.mark.parametrize("relation", ["continue", "rerun", "repair"])
def test_direct_branch_launch_creates_new_run_lineage_and_never_activates_workbench(tmp_path, monkeypatch, relation):
    settings, _ui, adapter = _source(tmp_path)
    source_before = {path.relative_to(adapter.root): path.read_bytes() for path in adapter.root.rglob("*") if path.is_file()}
    operations = Mock()

    def adopt(_title, worker, receive, **kwargs):
        return SimpleNamespace(worker=worker, receive=receive, kwargs=kwargs)

    operations.adopt.side_effect = adopt
    operations.assert_output_available = Mock()
    context = SimpleNamespace(settings=settings, operations=operations, api_key="test", models=["gpt-4.1"])
    monkeypatch.setattr("kajovo.studio.history_launcher.new_run_id", lambda: "RUN_TARGET")
    refreshed = Mock()
    launcher = HistoryBranchLauncher(context, refreshed)
    checkpoint = adapter.checkpoints()[0]
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], relation, "QA")
    record = launcher.launch(adapter, preview, "Zachovej přesný význam." if relation == "repair" else "")
    assert record.kwargs["identifier"] == "RUN_TARGET"
    assert operations.adopt.call_count == 1
    assert {path.relative_to(adapter.root): path.read_bytes() for path in adapter.root.rglob("*") if path.is_file()} == source_before
    target = LegacyRunAdapter(Path(settings.log_dir) / "RUN_TARGET")
    assert target.lineage()[0]["source_run_id"] == "RUN_SOURCE"
    assert target.lineage()[0]["relation_type"] == relation
    assert record.worker.cfg.recovery_instruction == ("Zachovej přesný význam." if relation == "repair" else "")
    with pytest.raises(ValueError, match="druhý submit"):
        launcher.launch(adapter, preview)


def test_repair_instruction_is_present_in_actual_new_qa_request(tmp_path, monkeypatch):
    settings, _ui, adapter = _source(tmp_path)
    operations = Mock()
    operations.assert_output_available = Mock()
    operations.adopt.side_effect = lambda _title, worker, receive, **kwargs: SimpleNamespace(worker=worker)
    context = SimpleNamespace(settings=settings, operations=operations, api_key="test", models=["gpt-4.1"])
    monkeypatch.setattr("kajovo.studio.history_launcher.new_run_id", lambda: "RUN_TARGET")
    launcher = HistoryBranchLauncher(context)
    checkpoint = adapter.checkpoints()[0]
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], "repair", "QA")
    worker = launcher.launch(adapter, preview, "Oprav pouze citaci.").worker
    worker._executor._create_response = Mock(return_value={"id": "resp_new", "status": "completed", "output_text": '{"text":"ok"}'})
    worker._executor._run_qa(Mock(), [], None)
    requests = LegacyRunAdapter(Path(settings.log_dir) / "RUN_TARGET").requests()
    payload = next(row["full_payload"]["payload"] for row in requests if row.get("request_record_id"))
    assert "Oprav pouze citaci." in str(payload["input"])


def test_edited_qa_rerun_instruction_is_present_in_actual_new_request(tmp_path, monkeypatch):
    settings, _ui, adapter = _source(tmp_path, status="completed")
    operations = Mock()
    operations.assert_output_available = Mock()
    operations.adopt.side_effect = lambda _title, worker, receive, **kwargs: SimpleNamespace(worker=worker)
    context = SimpleNamespace(settings=settings, operations=operations, api_key="test", models=["gpt-4.1"])
    monkeypatch.setattr("kajovo.studio.history_launcher.new_run_id", lambda: "RUN_QA_EDITED")
    launcher = HistoryBranchLauncher(context)
    checkpoint = adapter.checkpoints()[0]
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], "rerun", "QA")
    worker = launcher.launch(adapter, preview, "Zaměř odpověď na klávesovou navigaci.").worker
    worker._executor._create_response = Mock(return_value={"id": "resp_new", "status": "completed", "output_text": '{"text":"ok"}'})
    worker._executor._run_qa(Mock(), [], None)
    requests = LegacyRunAdapter(Path(settings.log_dir) / "RUN_QA_EDITED").requests()
    payload = next(row["full_payload"]["payload"] for row in requests if row.get("request_record_id"))
    assert "Zaměř odpověď na klávesovou navigaci." in str(payload["input"])


def test_existing_batch_blocks_new_branch_submit(tmp_path):
    settings, _ui, adapter = _source(tmp_path, batch=True)
    context = SimpleNamespace(settings=settings, operations=Mock(), api_key="test", models=["gpt-4.1"])
    launcher = HistoryBranchLauncher(context)
    with pytest.raises(ValueError, match="BATCH"):
        launcher.preview(adapter, adapter.checkpoints()[0]["checkpoint_id"], "rerun")


def test_output_lock_is_checked_before_target_run_is_created(tmp_path, monkeypatch):
    settings, _ui, adapter = _source(tmp_path, mode="QFILE")
    operations = SimpleNamespace(assert_output_available=Mock(side_effect=ValueError("obsazeno")))
    context = SimpleNamespace(settings=settings, operations=operations, api_key="test", models=["gpt-4.1"])
    monkeypatch.setattr("kajovo.studio.history_launcher.new_run_id", lambda: "RUN_MUST_NOT_EXIST")
    launcher = HistoryBranchLauncher(context)
    checkpoint = adapter.checkpoints()[0]
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], "rerun", "QFILE")
    with pytest.raises(ValueError, match="obsazeno"):
        launcher.launch(adapter, preview)
    assert not (Path(settings.log_dir) / "RUN_MUST_NOT_EXIST").exists()


def test_artifact_guard_accepts_sha_and_blocks_changed_missing_remote_and_source_overwrite(tmp_path):
    root = tmp_path / "RUN"
    root.mkdir()
    path = root / "file.txt"
    path.write_text("ok", encoding="utf-8")
    record = {"path_in_bundle": "file.txt", "sha256": hashlib.sha256(b"ok").hexdigest(), "available_local": True}
    guard = ArtifactGuard(root)
    assert guard.resolve(record) == path
    with pytest.raises(ValueError, match="přepsat"):
        guard.export(record, root / "copy.txt")
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        guard.resolve(record)
    path.unlink()
    with pytest.raises(ValueError, match="neexistuje"):
        guard.resolve(record)
    with pytest.raises(ValueError, match="vzdálený"):
        guard.resolve(record | {"available_local": False})


def test_modify_classification_uses_manifest_hash_and_explicit_events():
    payload = {
        "artifacts": [{"role": "modified_file", "reconstruction_role": "changed.py", "metadata": {"before_sha256": "a"}}],
        "events": [{"event_type": "fs.change", "data": {"action": "delete", "src": "old.py"}}],
    }
    state = {
        "preparation_snapshot": {"structure": {"touched_files": [
            {"path": "changed.py", "action": "modify"}, {"path": "new.py", "action": "add"},
            {"path": "skip.py", "action": "modify"}, {"path": "failed.py", "action": "modify"},
        ], "preserved_files": [{"path": "same.py"}]}},
        "completed_hashes": {"skip.py": "hash"}, "missing_deliverables": [{"path": "failed.py"}],
    }
    values = {row.path: row.classification for row in classify_modify_files(payload, state)}
    assert values == {"changed.py": "změněné", "new.py": "výsledek nezapsán", "skip.py": "výsledek nezapsán",
                      "failed.py": "chybové", "same.py": "zachované", "old.py": "odstraněné"}
    state["_verified_skip_paths"] = ["skip.py"]
    assert next(row for row in classify_modify_files(payload, state) if row.path == "skip.py").classification == "přeskočené · hash ověřen"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY", "QA", "QFILE"])
def test_new_standard_runs_receive_explicit_input_checkpoint(tmp_path, mode):
    settings = AppSettings(log_dir=str(tmp_path / mode))
    logger = RunLogger(settings.log_dir, f"RUN_{mode}", "P")
    ui = default_state(settings)
    ui.update(mode=mode, prompt="x", project="P")
    logger.update_state({"ui_state": ui})
    checkpoint = logger.bundle.checkpoints()[0]
    assert checkpoint["checkpoint_type"] == "input_ready"
    assert checkpoint["safe_to_continue"] is True
    assert logger.bundle.validate_checkpoint(checkpoint["checkpoint_id"])
