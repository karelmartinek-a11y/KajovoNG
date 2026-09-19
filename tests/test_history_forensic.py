"""Regrese odvozené z produkčního záznamu, nikoli pouze z ručně malovaných fází."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from kajovo.core.config import AppSettings
from kajovo.core.response_journal import ResponseJournal
from kajovo.core.run_bundle import HistoryIndex, LegacyRunAdapter
from kajovo.core.runlog import RunLogger
from kajovo.studio.history import HistoryPage
from kajovo.studio.history_artifacts import ArtifactBrowser
from kajovo.studio.history_data import HistoryData, checked_checkpoints, unique_responses
from kajovo.studio.history_launcher import HistoryBranchLauncher, first_paid_operation
from kajovo.studio.history_models import RunTableModel, build_run, build_stage
from kajovo.studio.history_policy import ActionAvailabilityPolicy
from kajovo.studio.history_timeline import RunTrackView
from kajovo.studio.operations import Operations
from kajovo.studio.workbench import default_state


def source(tmp_path, mode="GENERATE"):
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    logger = RunLogger(settings.log_dir, "RUN_FORENSIC", "Projekt")
    ui = default_state(settings)
    ui.update(mode=mode, project="Projekt", prompt="Uložené zadání", model="gpt-4.1")
    logger.update_state({"ui_state": ui})
    return settings, logger, ui


def test_wrapped_label_releases_old_height_after_panel_expands(qtbot):
    from kajovo.studio.components import caption
    label = caption("Delší text vybrané fáze musí po rozšíření panelu zabírat méně řádků.")
    qtbot.addWidget(label)
    label.resize(100, 500)
    label.show()
    qtbot.waitUntil(lambda: label.minimumHeight() > 50)
    narrow = label.minimumHeight()
    label.resize(600, 500)
    qtbot.waitUntil(lambda: label.minimumHeight() < narrow)


@pytest.mark.parametrize("relation", ["continue", "rerun", "repair"])
def test_async_branch_prepares_off_gui_and_adopts_real_worker(qtbot, tmp_path, monkeypatch, relation):
    from PySide6.QtCore import QThread
    from kajovo.studio.workers.run_worker import RunWorker

    settings, logger, _ui = source(tmp_path, "QA")
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    parent = QWidget()
    qtbot.addWidget(parent)
    operations = Operations(parent)
    context = SimpleNamespace(settings=settings, operations=operations, api_key="test", models=["gpt-4.1"])
    launcher = HistoryBranchLauncher(context)
    checkpoint = adapter.checkpoints()[0]
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], relation)
    original = adapter.bundle.verify_integrity
    threads = []

    def verify():
        threads.append(QThread.currentThread() == operations.thread())
        return original()

    monkeypatch.setattr(adapter.bundle, "verify_integrity", verify)
    monkeypatch.setattr(RunWorker, "run", lambda self: self.finished_ok.emit({"status": "completed"}))
    before = {str(path): path.read_bytes() for path in adapter.root.rglob("*") if path.is_file()}
    received = []
    launcher.launch_async(adapter, preview, receive=received.append)
    with pytest.raises(ValueError, match="již"):
        launcher.launch_async(adapter, preview)
    qtbot.waitUntil(lambda: bool(received), timeout=10000)
    record = received[0]
    qtbot.waitUntil(lambda: bool(record.terminal), timeout=10000)
    assert threads == [False]
    assert record.dialog is not None
    assert record.identifier != adapter.run_id
    target = LegacyRunAdapter(Path(settings.log_dir) / record.identifier)
    assert target.lineage()[0]["relation_type"] == relation
    assert before == {str(path): path.read_bytes() for path in adapter.root.rglob("*") if path.is_file()}
    for operation in operations.records.values():
        operation.dialog.close()


def test_actual_background_journal_and_received_copy_use_original_phase(tmp_path):
    _settings, logger, _ui = source(tmp_path)
    payload = {"model": "gpt-4.1", "input": "Návrh"}
    logger.save_json("requests", "A2_request", {"payload": payload})
    client = Mock()
    client.create_response.return_value = {"id": "resp_one", "status": "completed", "output_text": "Výsledek",
                                           "usage": {"input_tokens": 20, "output_tokens": 10}}
    response = ResponseJournal(logger).execute(client, payload, stopped=lambda: False,
                                               cancelled=lambda: False, progress=Mock())
    logger.save_json("responses", "received_resp_one", response)
    logger.save_json("responses", "A2_response", response)
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    assert [row["stage"] for row in adapter.steps()] == ["A2"]
    identifier = adapter.steps()[0]["step_id"]
    assert {row["step_id"] for row in adapter.requests() + adapter.responses()} == {identifier}
    assert len(adapter.responses()) == 3
    assert sum(row["output_tokens"] for row in unique_responses(adapter.responses())) == 10
    assert client.create_response.call_count == 1


def test_old_transport_steps_remain_raw_but_are_not_workflow_phases():
    steps = [{"step_id": name, "stage": name, "title": name, "status": "running"}
             for name in ("A0R", "background", "provider", "received", "A1", "A2")]
    run = build_run({"mode": "GENERATE", "status": "failed"}, steps=steps)
    assert [stage.stage for stage in run.stages] == ["A0R", "A1", "A2"]
    assert run.stages[0].title == "Upřesnění požadavků"
    assert run.stages[-1].status.label == "Konec fáze nezapsán"
    assert len(steps) == 6
    generic = build_run({"mode": "CUSTOM", "status": "running"}, steps=steps)
    assert len(generic.stages) == 6


def test_duration_is_never_sum_of_overlapping_phases():
    step = {"stage": "QA", "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:01:00Z"}
    run = build_run({"mode": "QA"}, steps=[step, step])
    assert run.duration is None
    assert build_stage(step).duration == 60


def test_bundle_export_validates_artifacts_and_preserves_destination_on_failure(tmp_path):
    from kajovo.studio.history_artifacts import export_run_bundle
    import zipfile

    _settings, logger, _ui = source(tmp_path)
    input_file = tmp_path / "file.txt"
    input_file.write_text("Původní obsah", encoding="utf-8")
    artifact = logger.bundle.archive_artifact(input_file, role="input", kind="input_file")
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    target = tmp_path / "export.zip"
    export_run_bundle(adapter, target)
    assert zipfile.is_zipfile(target)
    original = target.read_bytes()
    with pytest.raises(ValueError, match="přepsat"):
        export_run_bundle(adapter, adapter.root / "export.zip")
    (adapter.root / artifact["path_in_bundle"]).write_text("Změněný obsah", encoding="utf-8")
    with pytest.raises(ValueError):
        export_run_bundle(adapter, target)
    assert target.read_bytes() == original


def test_programmatic_phase_selection_updates_visible_text_and_copy(qtbot, tmp_path):
    from kajovo.studio.history_details import RunDetailView
    from kajovo.studio.history_data import payload

    settings, logger, _ui = source(tmp_path)
    for stage, answer in (("A0R", "První výstup"), ("A1", "Druhý výstup")):
        logger.save_json("requests", stage + "_request", {"payload": {"model": "gpt-4.1", "input": stage}})
        logger.save_json("responses", stage + "_response", {"id": "resp_" + stage, "status": "completed", "output_text": answer})
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    data = payload(adapter)
    run = build_run(data["summary"], steps=data["steps"])
    context = SimpleNamespace(settings=settings, operations=Mock())
    from kajovo.core.batch_completion import read_state
    view = RunDetailView(context, adapter, data, read_state(adapter.root), run)
    qtbot.addWidget(view)
    assert view.phase_text.text == "Druhý výstup"
    view.select_stage(run, run.stages[0])
    assert view.phase_text.text == view.phase_text.editor.toPlainText() == "První výstup"
    assert view.phase_text.copy_button.isEnabled()


@pytest.mark.parametrize("recovered", [False, True])
def test_detail_exposes_errors_and_read_only_output_check(qtbot, tmp_path, recovered):
    from kajovo.core.batch_completion import read_state
    from kajovo.core.contracts import ContractError
    from kajovo.studio.history_data import payload
    from kajovo.studio.history_details import RunDetailView

    settings, logger, _ui = source(tmp_path)
    logger.begin_validated_step("A1", kind="preparation")
    if recovered:
        logger.event("response.poll_error", {"response_id": "resp", "status_code": 503})
        logger.event("response.poll_recovered", {"response_id": "resp", "failed_attempts": 1})
    else:
        logger.exception("run", ContractError("Neplatná vazba na požadavek."))
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    data = payload(adapter)
    run = build_run(data["summary"], steps=data["steps"])
    operations = Mock()
    context = SimpleNamespace(settings=settings, operations=operations)
    view = RunDetailView(context, adapter, data, read_state(adapter.root), run)
    qtbot.addWidget(view)
    assert "Chyby" in [view.tabs.tabText(index) for index in range(view.tabs.count())]
    view.check_outputs()
    args = operations.start_read.call_args.args
    assert args[1](None) == []
    args[2]([{"path": "main.py", "current_status": "missing"}])
    assert "main.py: chybí" in view.output_status.toPlainText()
    assert view.output_status.isReadOnly()


def test_cached_index_refresh_does_not_read_responses_or_rescan_lineage(tmp_path, monkeypatch):
    settings, logger, _ui = source(tmp_path)
    logger.save_json("responses", "QA_response", {"id": "resp_lookup", "status": "completed"})
    index = HistoryIndex(settings.log_dir)
    index.refresh()
    original = index.path.stat().st_mtime_ns
    monkeypatch.setattr(LegacyRunAdapter, "responses", Mock(side_effect=AssertionError("Plná odpověď při refresh")))
    monkeypatch.setattr(LegacyRunAdapter, "lineage", Mock(side_effect=AssertionError("Druhý sken návazností")))
    assert "resp_lookup" in index.refresh()[0]["search_text"]
    assert index.reverse_lineage() == {}
    assert index.path.stat().st_mtime_ns == original


def test_metadata_selection_never_reads_full_responses_or_hashes_bundle(tmp_path, monkeypatch):
    _settings, logger, _ui = source(tmp_path)
    monkeypatch.setattr(LegacyRunAdapter, "responses", Mock(side_effect=AssertionError("Plné odpovědi")))
    monkeypatch.setattr(LegacyRunAdapter, "integrity", Mock(side_effect=AssertionError("Úplná integrita")))
    from kajovo.core import run_bundle
    reader = run_bundle._read_jsonl
    def read(path):
        assert Path(path).name != "events.jsonl", "Čtení metadat načítá celý deník událostí"
        return reader(path)
    monkeypatch.setattr(run_bundle, "_read_jsonl", read)
    _adapter, payload, _state = HistoryData().read(Path(logger.paths.run_dir))
    assert "responses" not in payload
    assert "integrity" not in payload


def test_opening_existing_bundle_does_not_create_missing_directories(tmp_path):
    _settings, logger, _ui = source(tmp_path)
    root = Path(logger.paths.run_dir)
    (root / "reports").rmdir()
    LegacyRunAdapter(root).run_record()
    assert not (root / "reports").exists()


def test_detail_cache_invalidates_on_canonical_change(tmp_path, monkeypatch):
    _settings, logger, _ui = source(tmp_path)
    data = HistoryData()
    root = Path(logger.paths.run_dir)
    original = LegacyRunAdapter.responses
    reader = Mock(side_effect=original)
    monkeypatch.setattr(LegacyRunAdapter, "responses", lambda self: reader(self))
    data.read(root, detail=True)
    data.read(root, detail=True)
    assert reader.call_count == 1
    logger.save_json("responses", "QA_response", {"id": "resp_new", "status": "completed"})
    assert data.read(root, detail=True)[1]["responses"][0]["response_id"] == "resp_new"
    assert reader.call_count == 2


@pytest.mark.parametrize("mode,comic,batch,edit", [
    ("GENERATE", False, False, False), ("QA", False, False, True),
    ("COMIC", True, False, False), ("MODIFY", False, True, False),
])
def test_every_domain_action_has_context_policy(mode, comic, batch, edit):
    state = {"ui_state": {"mode": mode}, "comic_operation_id": "comic" if comic else ""}
    if batch:
        state["batch_id"] = "batch_1"
    decisions = ActionAvailabilityPolicy().evaluate({"mode": mode, "status": "completed"}, state, [], legacy=False)
    assert decisions["comic"].visible is comic
    assert decisions["open_batch"].visible is batch
    assert decisions["complete_batch"].visible is batch
    assert decisions["edit_branch"].visible is edit
    assert decisions["repair"].visible is False


def test_unverified_checkpoint_and_submission_unknown_never_enable_branch():
    policy = ActionAvailabilityPolicy()
    for status, checkpoint in (("failed", {"safe_to_continue": True}),
                               ("submission_unknown", {"safe_to_continue": True, "_availability_valid": True})):
        decisions = policy.evaluate({"mode": "QA", "status": status}, {"ui_state": {"mode": "QA"}},
                                    [checkpoint], legacy=False)
        assert all(not decisions[name].enabled for name in policy.DIRECT)


def test_selected_segment_survives_row_change_and_async_loading(qtbot, tmp_path):
    settings, logger, _ui = source(tmp_path)
    step = logger.bundle.ensure_step("A2")
    host = QWidget()
    qtbot.addWidget(host)
    manager = Operations(host)
    context = SimpleNamespace(settings=settings, operations=manager, api_key="", models=[])
    page = HistoryPage(context, Mock())
    qtbot.addWidget(page)
    run = build_run(logger.bundle.run_record(), steps=logger.bundle.steps())
    page.model.set_runs([run])
    page.resize(1400, 900)
    page.show()
    page.tracks.setColumnWidth(1, 800)
    index = page.model.index(0, 1)
    rectangle = page.tracks.delegate._rectangles(run, page.tracks.visualRect(index))[0][1]
    QTest.mouseClick(page.tracks.viewport(), Qt.LeftButton, pos=rectangle.center().toPoint())
    qtbot.waitUntil(lambda: page.adapter is not None and not manager.active, timeout=10000)
    assert page.selected_step["step_id"] == step["step_id"]
    assert page.tracks.delegate.selected_step == step["step_id"]
    assert page.buttons["comic"].isHidden()
    assert page.buttons["edit_branch"].isHidden()


def test_keyboard_selects_phases_without_creating_widgets(qtbot):
    stages = [{"step_id": str(i), "stage": "A" + str(i), "status": "completed"} for i in (1, 2)]
    model = RunTableModel()
    model.set_runs([build_run({"run_id": "RUN", "mode": "GENERATE"}, steps=stages)])
    view = RunTrackView()
    qtbot.addWidget(view)
    view.setModel(model)
    view.setCurrentIndex(model.index(0, 1))
    QTest.keyClick(view, Qt.Key_Right)
    assert view.delegate.selected_step == "1"
    QTest.keyClick(view, Qt.Key_Right)
    assert view.delegate.selected_step == "2"


def test_corrupt_artifact_disables_open_after_real_preview(qtbot, tmp_path):
    _settings, logger, _ui = source(tmp_path)
    path = tmp_path / "input.txt"
    path.write_text("původní", encoding="utf-8")
    record = logger.bundle.archive_artifact(path, role="user_input")
    (Path(logger.paths.run_dir) / record["path_in_bundle"]).write_text("změněný", encoding="utf-8")
    view = ArtifactBrowser()
    qtbot.addWidget(view)
    view.set_artifacts(logger.paths.run_dir, [record])
    assert not view.buttons["open"].isEnabled()
    assert not view.buttons["copy"].isEnabled()
    assert "SHA-256" in view.notice.text()


def test_required_response_hash_and_checkpoint_path_are_validated(tmp_path):
    _settings, logger, _ui = source(tmp_path)
    response = logger.bundle.record_response({"id": "resp", "status": "completed"}, name="A1")
    checkpoint = logger.bundle.checkpoint("test", state_snapshot={}, safe_to_continue=True,
                                          required_response_ids=[response["response_record_id"]], reason="Uložená odpověď")
    path = logger.bundle.responses_dir / ("_record_" + response["response_record_id"] + ".json")
    response["full_response"]["status"] = "failed"
    path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        logger.bundle.validate_checkpoint(checkpoint["checkpoint_id"])
    with pytest.raises(ValueError, match="identifikátor"):
        logger.bundle.validate_checkpoint("../run_state")


def test_input_tree_is_reconstructed_from_archive_even_after_original_changes(tmp_path, monkeypatch):
    settings, logger, ui = source(tmp_path, "MODIFY")
    inputs = tmp_path / "IN"
    inputs.mkdir()
    (inputs / "test.txt").write_text("původní", encoding="utf-8")
    ui["in_dir"] = str(inputs)
    logger.update_state({"ui_state": ui})
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    checkpoint = adapter.checkpoints()[-1]
    assert checked_checkpoints(adapter, [checkpoint], adapter.artifacts())[0]["_availability_valid"]
    (inputs / "test.txt").write_text("nový obsah", encoding="utf-8")
    operations = Mock()
    operations.assert_output_available = Mock()
    operations.adopt.side_effect = lambda _title, worker, **kwargs: SimpleNamespace(worker=worker)
    context = SimpleNamespace(settings=settings, operations=operations, api_key="test", models=["gpt-4.1"])
    launcher = HistoryBranchLauncher(context)
    monkeypatch.setattr("kajovo.studio.history_launcher.new_run_id", lambda: "RUN_NEW")
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], "rerun")
    worker = launcher.launch(adapter, preview).worker
    assert Path(worker.cfg.in_dir) != inputs
    assert (Path(worker.cfg.in_dir) / "test.txt").read_text(encoding="utf-8") == "původní"


def test_cascade_preview_uses_checkpoint_next_step_not_selected_completed_step():
    checkpoint = {"state_snapshot": {"next_step_id": "validation"}}
    assert first_paid_operation("KASKADA", checkpoint, "analysis") == "validation"


def test_local_reads_do_not_accumulate_results_or_hidden_dialogs(qtbot):
    from kajovo.studio.operations import OperationDialog
    host = QWidget()
    qtbot.addWidget(host)
    manager = Operations(host)
    received = []
    for _ in range(40):
        manager.start_read("Čtení", lambda task: {"text": "x" * 100000}, received.append)
    qtbot.waitUntil(lambda: not manager.active, timeout=10000)
    assert len(received) == 40
    assert manager.records == {}
    assert host.findChildren(OperationDialog) == []


def test_identical_poll_result_does_not_rewrite_journal_state(tmp_path):
    _settings, logger, _ui = source(tmp_path)
    journal = ResponseJournal(logger)
    response = {"id": "resp", "status": "in_progress", "_request_id": "get_new"}
    entry = {"id": "resp", "status": "in_progress", "response": response | {"_request_id": "get_old"}}
    logger.save_json = Mock(side_effect=AssertionError("Opakovaný zápis stejných dat"))
    logger.update_state = Mock(side_effect=AssertionError("Opakovaný zápis stavu"))
    for _ in range(100):
        journal._record("key", entry, response)
    assert entry["status"] == "in_progress"
    assert entry["checked_at"] > 0


def test_text_card_missing_content_has_no_enabled_copy_or_save(qtbot, tmp_path):
    from kajovo.studio.history_overview import TextCard
    card = TextCard("Odpověď", "", tmp_path)
    qtbot.addWidget(card)
    assert not card.copy_button.isEnabled()
    assert not card.save_button.isEnabled()


def test_batch_repair_instruction_is_in_new_requests_without_changing_snapshot():
    from test_generate_batch import specification
    from kajovo.core.generate_batch import build_manifest
    ordinary = build_manifest("original", "Zadání", {"contract": "A1_PLAN"}, specification(), "gpt-4.1-nano", 0)
    branch = build_manifest("branch", "Zadání", {"contract": "A1_PLAN"}, specification(), "gpt-4.1-nano", 0,
                            recovery_instruction="Oprav přesný typ návratové hodnoty.")
    assert branch["snapshot"] == ordinary["snapshot"]
    assert branch["snapshot_hash"] == ordinary["snapshot_hash"]
    for request in branch["requests"]:
        assert json.loads(request["body"]["input"])["recovery_instruction"] == "Oprav přesný typ návratové hodnoty."
        schema = request["body"]["text"]["format"]["schema"]
        assert schema["properties"] == {"content": {"type": "string"}}
        assert schema["required"] == ["content"]
        assert schema["additionalProperties"] is False
