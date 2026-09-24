"""Revize formuláře a výchozí volba bezpečného MODIFY."""

from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from kajovo.core.config import AppSettings
from kajovo.studio.context import StudioContext
from kajovo.studio.workbench import Workbench, default_state


@pytest.fixture
def workbench(qtbot, tmp_path, monkeypatch):
    operations = Mock(active=[], assert_output_available=Mock())
    operations.adopt.return_value.dialog.notification.isChecked.return_value = False
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"), dry_run_modify=True)
    context = StudioContext(settings, operations, api_key="test")
    context.models = ["gpt-4.1"]
    page = Workbench(context)
    qtbot.addWidget(page)
    monkeypatch.setattr("kajovo.studio.workbench.RunWorker", Mock())
    return page


def test_modify_default_and_explicit_loaded_choice(workbench):
    assert default_state(workbench.context.settings)["dry_run"] is False
    assert workbench.state()["dry_run"] is False
    assert workbench.config().dry_run is False
    workbench.widgets["mode"].setCurrentIndex(workbench.widgets["mode"].findData("MODIFY"))
    assert workbench.config().dry_run is True
    workbench.apply_state({"mode": "MODIFY", "dry_run": False})
    assert workbench.config().dry_run is False
    workbench.apply_state({"mode": "MODIFY"})
    assert workbench.config().dry_run is True
    workbench.widgets["dry_run"].setChecked(False)
    workbench.widgets["mode"].setCurrentIndex(workbench.widgets["mode"].findData("QA"))
    workbench.widgets["mode"].setCurrentIndex(workbench.widgets["mode"].findData("MODIFY"))
    assert workbench.config().dry_run is False


@pytest.mark.parametrize("mode", ["GENERATE", "QA", "QFILE"])
def test_non_modify_saved_state_never_inherits_modify_preference(workbench, mode):
    workbench.widgets["mode"].setCurrentIndex(workbench.widgets["mode"].findData("MODIFY"))
    assert workbench.state()["dry_run"] is True
    workbench.widgets["mode"].setCurrentIndex(workbench.widgets["mode"].findData(mode))
    assert workbench.state()["dry_run"] is False
    assert workbench.config().dry_run is False
    assert not workbench.widgets["dry_run"].isChecked()


def test_history_generate_config_does_not_inherit_modify_preference(workbench, tmp_path, monkeypatch):
    from kajovo.studio.history_launcher import HistoryBranchLauncher

    workbench.apply_state({"mode": "GENERATE", "project": "Projekt", "model": "gpt-4.1", "prompt": "Zadání"})
    ui = workbench.state()
    assert ui["dry_run"] is False
    source = tmp_path / "source"
    source.mkdir()
    (source / "run_state.json").write_text("{}", encoding="utf-8")
    factory = Mock()
    monkeypatch.setattr("kajovo.studio.history_launcher.RunWorker", factory)
    preview = SimpleNamespace(relation="rerun", checkpoint_type="input_ready", checkpoint_id="cp", source_run_id="source")
    HistoryBranchLauncher(workbench.context)._standard_worker(
        "RUN_HISTORY", SimpleNamespace(root=source), ui, {"ui_state": ui}, preview, ""
    )
    assert factory.call_args.args[0].mode == "GENERATE"
    assert factory.call_args.args[0].dry_run is False


@pytest.mark.parametrize("change", ["prompt", "restore", "load", "attachment", "parameter", "new_run", None])
@pytest.mark.parametrize("mode", ["QA", "QFILE"])
def test_result_changes_only_original_form_revision(workbench, tmp_path, change, mode):
    workbench.apply_state({"project": "Projekt", "prompt": "Původní", "model": "gpt-4.1",
                           "mode": mode, "out_dir": str(tmp_path / "out"), "qfile_suggest_path": True,
                           "qa_continue_conversation": mode == "QA"})
    assert workbench.start() is not None
    receive = workbench.context.operations.adopt.call_args.args[2]
    if change in {"prompt", "restore"}:
        workbench.prompt.setPlainText("Nové")
        if change == "restore":
            workbench.prompt.setPlainText("Původní")
    elif change == "load":
        workbench.apply_state(workbench.state())
    elif change == "attachment":
        workbench.context.files.append("file-new")
        workbench.context.attachments_changed.emit()
    elif change == "parameter":
        workbench.widgets["temperature"].setValue(0.6)
    elif change == "new_run":
        workbench.busy_outputs.clear()
        assert workbench.start() is not None
    before = workbench.state()
    result = {"status": "qfile_plan_ready" if mode == "QFILE" else "completed", "response_id": "old-result",
              "qfile_plan": {"proposed_path": "navrh.md", "format": "md"}}
    receive(result)
    if change:
        assert workbench.state() == before
    else:
        assert workbench.widgets["response_id"].text() == "old-result"
        if mode == "QFILE":
            assert workbench.state()["qfile_output_path"] == "navrh.md"
            assert workbench.state()["qfile_plan"] == result["qfile_plan"]
    assert workbench.result.value == result
