"""Workbench smí přijmout pouze editovatelný clone intent, ne skryté resume."""

from unittest.mock import Mock
import json

from kajovo.core.config import AppSettings
from kajovo.studio.context import StudioContext
from kajovo.studio.workbench import Workbench


def test_workbench_has_no_history_resume_state(qtbot, tmp_path):
    context = StudioContext(AppSettings(log_dir=str(tmp_path / "LOG")), Mock(), api_key="test")
    workbench = Workbench(context)
    qtbot.addWidget(workbench)
    assert not hasattr(workbench, "resume")
    assert not hasattr(workbench, "resume_submitted")
    assert not hasattr(workbench, "resume_notice")
    assert workbench.start_button.text() == "Spustit práci"


def test_clarification_updates_task_without_hidden_history(qtbot, tmp_path, monkeypatch):
    context = StudioContext(AppSettings(log_dir=str(tmp_path / "LOG")), Mock(), api_key="test")
    workbench = Workbench(context)
    qtbot.addWidget(workbench)
    workbench.widgets["response_id"].setText("resp_old")
    monkeypatch.setattr("kajovo.studio.workbench.QInputDialog.getMultiLineText", lambda *args: ("Česky.", True))
    workbench.resolve_questions({"questions": [{"question": "Jaký jazyk?"}]}, "Vytvořit dokument.")
    assert "Vytvořit dokument." in workbench.prompt.toPlainText()
    assert "Česky." in workbench.prompt.toPlainText()
    assert workbench.widgets["response_id"].text() == ""


def test_clone_lineage_is_written_only_when_new_workbench_run_starts(qtbot, tmp_path, monkeypatch):
    operations = Mock()
    operations.active = []
    operations.assert_output_available = Mock()
    operations.adopt.return_value = Mock(
        dialog=Mock(notification=Mock(show=Mock())),
        worker=Mock(finished=Mock(connect=Mock())),
    )
    context = StudioContext(AppSettings(log_dir=str(tmp_path / "LOG")), operations, api_key="test")
    context.models = ["gpt-4.1"]
    workbench = Workbench(context)
    qtbot.addWidget(workbench)
    state = workbench.state()
    state.update(project="Projekt", prompt="Nové zadání", model="gpt-4.1", mode="QA")
    workbench.apply_state(state)
    workbench.pending_lineage = {"source_run_id": "RUN_SOURCE", "relation_type": "clone"}
    assert not (tmp_path / "LOG").exists()
    monkeypatch.setattr("kajovo.studio.workbench.RunWorker", Mock())
    workbench.start()
    targets = [path for path in (tmp_path / "LOG").iterdir() if path.is_dir()]
    assert len(targets) == 1
    lineage = json.loads((targets[0] / "lineage.json").read_text(encoding="utf-8"))
    assert any(row["relation_type"] == "clone" for row in lineage["records"])
    assert workbench.pending_lineage is None
