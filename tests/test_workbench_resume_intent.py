"""Viditelný záměr pokračování a ochrana před opakovaným odesláním."""
from copy import deepcopy
from unittest.mock import Mock

from kajovo.core.config import AppSettings
from kajovo.core.generate_batch import digest
from kajovo.studio.context import StudioContext
from kajovo.studio.workbench import Workbench
from test_preparation_snapshot import snapshot


def test_resume_intent_blocks_changes_and_double_submission(qtbot, tmp_path, monkeypatch):
    context = StudioContext(AppSettings(log_dir=str(tmp_path / "LOG")), Mock(), api_key="test")
    context.models = ["gpt-4.1"]
    context.operations.active = []
    context.operations.assert_output_available = Mock()
    workbench = Workbench(context)
    qtbot.addWidget(workbench)
    ui = workbench.state()
    ui.update(model="gpt-4.1", mode="GENERATE", prompt="test", project="Projekt", out_dir=str(tmp_path / "out"))
    workbench.apply_state(ui)
    saved = snapshot(stage="A1")
    saved["structure"] = None
    saved.pop("snapshot_hash")
    saved["snapshot_hash"] = digest(saved)
    original = deepcopy(saved)
    workbench.resume = {"preparation_snapshot": saved, "completed_hashes": {}}
    workbench.pending_lineage = {"source_run_id": "RUN_source", "source_checkpoint_id": "safe", "relation_type": "continue"}
    assert workbench.validate()
    assert workbench.start_button.text() == "Pokračovat od A2"
    assert workbench.start_button.accessibleName() == workbench.start_button.text()
    assert "A0R, A1" in workbench.resume_notice.text()
    worker = Mock()
    monkeypatch.setattr("kajovo.studio.workbench.RunWorker", worker)
    workbench.prompt.setPlainText("Jiné zadání")
    workbench.start()
    worker.assert_not_called()
    assert not workbench.start_button.isEnabled()
    workbench.prompt.setPlainText("test")
    assert workbench.validate()
    workbench.start()
    worker.assert_called_once()
    assert worker.call_args.args[0].preparation_snapshot == original
    assert workbench.resume_submitted
    assert not workbench.start_button.isEnabled()
    workbench.start()
    worker.assert_called_once()
    assert saved == original
    workbench.reset()
    assert not workbench.resume and not workbench.resume_submitted
    assert workbench.resume_notice.isHidden()
    assert workbench.start_button.text() == "Spustit práci"


def test_resume_quality_gate_and_file_stage_labels(qtbot):
    context = StudioContext(AppSettings(), Mock(), api_key="test")
    workbench = Workbench(context)
    qtbot.addWidget(workbench)
    for mode, prefix in (("GENERATE", "A"), ("MODIFY", "B")):
        for quality, stage, next_stage in ((False, "2", "3"), (True, "2", "2Q"), (True, "2Q", "3")):
            workbench.resume = {"preparation_snapshot": snapshot(mode, quality, prefix + stage)}
            workbench.update_resume_notice()
            assert workbench.start_button.text() == "Pokračovat od " + prefix + next_stage
