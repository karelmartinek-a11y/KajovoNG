"""Pracovní kontrakty nové sestavy bez síťových služeb a provozních dat."""

import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QThread, Signal

from kajovo.core.config import AppSettings
from kajovo.core.progress import ProgressEvent
from kajovo.studio.application import create_window
from kajovo.studio.operations import Operations


@pytest.fixture
def studio(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = Mock()
    client.list_models.return_value = [{"id": "gpt-4.1"}]
    widget = create_window(AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
                           api_key="test-key", client_factory=lambda *args, **kwargs: client)
    qtbot.addWidget(widget)
    widget.context.models = ["gpt-4.1"]
    widget.context.models_changed.emit()
    widget.resize(1366, 900)
    yield widget
    for manager in widget.findChildren(Operations):
        for record in manager.active:
            if hasattr(record.worker, "request_stop"):
                record.worker.request_stop()
        qtbot.waitUntil(lambda current=manager: not current.active, timeout=10000)


def test_factory_installs_all_production_pages_without_remote_reads(studio):
    assert set(studio.pages) == {"run", "photos", "comics", "cascade", "resources", "batch", "history", "versions", "models", "settings", "help"}
    assert not studio.context.client().mock_calls
    for page in studio.pages.values():
        assert type(page).__module__.startswith(("kajovo.studio", "PySide6"))


def test_saved_state_preserves_parameters_without_password(studio, tmp_path):
    workbench = studio.workbench
    values = workbench.state()
    values.update(project="Projekt", model="gpt-4.1", temperature=0.7,
                  in_dir=str(tmp_path), out_dir=str(tmp_path / "out"), prompt="Zadání",
                  ssh_password="test-password", maximum_quality=True)
    workbench.apply_state(values)
    result = workbench.state()
    assert result["temperature"] == 0.7
    assert result["maximum_quality"] is True
    assert result["ssh_password"] == ""
    assert workbench.config().ssh_password == "test-password"


def test_batch_clears_incompatible_output_diagnostics(studio):
    workbench = studio.workbench
    workbench.widgets["diag_windows_out"].setChecked(True)
    workbench.widgets["diag_ssh_out"].setChecked(True)
    workbench.widgets["send_as_c"].setChecked(True)
    assert not workbench.widgets["diag_windows_out"].isChecked()
    assert not workbench.widgets["diag_ssh_out"].isChecked()
    assert not workbench.widgets["diag_windows_out"].isEnabled()


def test_catalog_reload_selects_recommended_compatible_model(studio):
    state = studio.workbench.state()
    state["model"] = "missing-model"
    studio.workbench.apply_state(state)
    assert studio.workbench.state()["model"] == "missing-model"
    studio.workbench.refresh_models()
    assert studio.workbench.state()["model"] == "gpt-4.1"


def test_failed_key_persistence_does_not_change_account(studio, monkeypatch):
    monkeypatch.setattr("kajovo.studio.settings.persist_api_key", Mock(side_effect=OSError("Úložiště selhalo")))
    studio.settings_page.key.setText("test-new-key")
    studio.settings_page.save_key()
    assert studio.context.api_key == "test-key"


def test_completion_waits_for_thread_and_output_lock(studio, qtbot, tmp_path):
    release = threading.Event()
    started = threading.Event()
    received = []

    def operation(task):
        started.set()
        release.wait(4)
        return "výsledek"

    record = studio.operations.start("Zápis", operation, received.append, output_dir=tmp_path / "out")
    try:
        qtbot.waitUntil(started.is_set)
        with pytest.raises(ValueError, match="zapisuje"):
            studio.operations.assert_output_available(tmp_path / "out" / "nested")
        record.dialog.close()
        assert record in studio.operations.active
        assert not received
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert received == ["výsledek"]
    studio.operations.assert_output_available(tmp_path / "out")


@pytest.mark.parametrize("state", ["partial", "cancelled", "batch_pending", "response_pending", "submission_unknown", "dry_run"])
def test_terminal_state_is_not_replaced_by_success(studio, qtbot, state):
    class Worker(QThread):
        progress_event = Signal(object)
        finished_ok = Signal(dict)
        finished_err = Signal(str)
        failure_detail = Signal(object)

        def run(self):
            self.progress_event.emit(ProgressEvent("RUN", state))
            self.finished_ok.emit({"status": state})

    record = studio.operations.adopt("Ukázková práce", Worker())
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == state
    assert not record.dialog.timer.isActive()
    assert not record.dialog.mark.running


def test_compact_workbench_has_visible_content_and_start(studio, qtbot):
    studio.resize(640, 360)
    studio.show()
    qtbot.wait(50)
    assert studio.size().width() == 640
    assert studio.size().height() == 360
    assert not studio.navigation_area.isVisible()
    assert studio.workbench.tabs.height() >= 140
    assert studio.workbench.start_button.isVisibleTo(studio)
    point = studio.workbench.start_button.mapTo(studio, studio.workbench.start_button.rect().bottomRight())
    assert studio.rect().contains(point)


def test_resources_attach_and_detach_change_run_config(studio):
    from kajovo.studio.resources import fill_records
    page = studio.resources
    fill_records(page.lists["files"], [{"id": "file_test", "filename": "zadani.txt"}])
    page.lists["files"].item(0).setSelected(True)
    page.attach("files")
    assert studio.workbench.config().input_file_ids == ["file_test"]
    page.detach("files")
    assert studio.workbench.config().input_file_ids == []


def test_git_editor_refuses_external_change(tmp_path):
    from kajovo.core.project_git import ProjectGit
    path = tmp_path / "file.txt"
    path.write_text("Původní", encoding="utf-8")
    service = ProjectGit(tmp_path)
    _, digest = service.read_file("file.txt")
    path.write_text("Jiná změna", encoding="utf-8")
    with pytest.raises(ValueError, match="změnil"):
        service.write_file("file.txt", "Přepsat", digest)
    assert path.read_text(encoding="utf-8") == "Jiná změna"


def test_git_editor_refuses_path_outside_project(tmp_path):
    from kajovo.core.project_git import ProjectGit
    with pytest.raises(ValueError):
        ProjectGit(tmp_path).read_file("../outside.txt")


def test_no_studio_module_imports_legacy_ui():
    root = Path(__file__).resolve().parents[1] / "kajovo" / "studio"
    for path in root.rglob("*.py"):
        assert "kajovo.desktop" not in path.read_text(encoding="utf-8")


def test_repeated_monitoring_reuses_dialog(studio, qtbot):
    first = studio.operations.start("Sledování", lambda task: 1, popup=False, identifier="monitor")
    qtbot.waitUntil(lambda: bool(first.terminal))
    second = studio.operations.start("Sledování", lambda task: 2, popup=False, identifier="monitor")
    qtbot.waitUntil(lambda: bool(second.terminal))
    assert first.dialog is second.dialog
    assert len(studio.operations.records) == 1
    assert second.result == 2
    assert not second.dialog.timer.isActive()


def test_photo_batch_is_not_done_before_local_download(studio, qtbot):
    from types import SimpleNamespace
    result = SimpleNamespace(batch_id="batch_test", status="completed")
    record = studio.operations.start("Fotografie", lambda task: result, popup=False)
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == "batch_pending"
    assert record.dialog.result is result


def test_detached_page_returns_same_content(studio, qtbot):
    studio.select_page("settings")
    page = studio.settings_page
    page.editors["default_model"].setText("Rozepsaná změna")
    studio.detach_page()
    dialog = studio.detached["settings"]
    assert page.isVisibleTo(dialog)
    assert page.isAncestorOf(page.editors["default_model"])
    dialog.accept()
    qtbot.waitUntil(lambda: not studio.detached)
    assert studio.settings_page is page
    assert page.editors["default_model"].text() == "Rozepsaná změna"
    assert studio.stack.widget(studio.stack.currentIndex()).widget() is page
    studio.detach_page()
    dialog = studio.detached["settings"]
    studio.select_page("photos")
    dialog.accept()
    assert studio.stack.currentIndex() == list(studio.pages).index("photos")
    assert studio.heading.text() == "Fotografie"


def test_default_model_change_updates_settings_editor(studio, monkeypatch):
    monkeypatch.setattr("kajovo.core.config.save_settings", Mock())
    studio.models.render()
    studio.models.listing.setCurrentRow(0)
    studio.models.set_default()
    assert studio.settings_page.snapshot().default_model == "gpt-4.1"


def test_duplicate_cascade_remaps_internal_input_reference(studio):
    from kajovo.core.cascade_types import CascadeInput, CascadeOutput
    page = studio.cascades
    page.add_step()
    original = page.selected()
    original.inputs = [CascadeInput(name="Zdroj")]
    original.outputs = [CascadeOutput(name="Změna", modify_input_id=original.inputs[0].id)]
    page.duplicate_step()
    duplicate = page.selected()
    assert duplicate.inputs[0].id != original.inputs[0].id
    assert duplicate.outputs[0].modify_input_id == duplicate.inputs[0].id


def test_history_clone_does_not_reuse_remote_response(studio, monkeypatch, tmp_path):
    from types import SimpleNamespace
    studio.history.adapter = SimpleNamespace(root=tmp_path, run_id="RUN_source")
    state = studio.workbench.state()
    state["response_id"] = "resp_source"
    studio.history._state = {"ui_state": state}
    studio.history.clone()
    assert studio.workbench.config().response_id == ""
    assert studio.workbench.pending_lineage["source_run_id"] == "RUN_source"


def test_history_clone_with_artifact_has_isolated_input(studio, tmp_path):
    import hashlib
    from types import SimpleNamespace
    source = tmp_path / "archive"
    source.mkdir()
    (source / "approved.txt").write_bytes(b"approved")
    (source / "unrelated.txt").write_bytes(b"unrelated")
    studio.history.adapter = SimpleNamespace(root=source, run_id="RUN_source", legacy=False)
    studio.history.payload = {"artifacts": [{"artifact_id": "artifact_test", "reusable": True,
                                            "path_in_bundle": "approved.txt", "sha256": hashlib.sha256(b"approved").hexdigest()}]}
    studio.history._state = {"ui_state": studio.workbench.state()}
    studio.history.clone_with_artifact(studio.history.payload["artifacts"][0])
    directory = Path(studio.workbench.widgets["in_dir"].text())
    assert directory != source
    assert [path.name for path in directory.iterdir()] == ["approved.txt"]
    assert studio.workbench.pending_lineage["inherited_artifact_ids"] == ["artifact_test"]


def test_history_page_has_no_old_workbench_resume_path(studio):
    assert not hasattr(studio.history, "resume")
    assert not hasattr(studio.workbench, "resume")
    assert not hasattr(studio.workbench, "resume_notice")


def test_photo_submission_uses_only_explicit_selection(studio, monkeypatch, tmp_path):
    from PySide6.QtCore import Qt
    page = studio.photos
    paths = [tmp_path / "first.png", tmp_path / "second.png"]
    from PIL import Image
    for path in paths:
        Image.new("RGB", (8, 6), (10, 20, 30)).save(path, format="PNG")
    page.add_paths([str(path) for path in paths])
    page.photos.clearSelection()
    page.photos.item(1).setSelected(True)
    studio.context.models = ["gpt-image-1.5"]
    studio.context.models_changed.emit()
    page.prompt.setPlainText("Upravit vybranou fotografii")
    page.output.setText(str(tmp_path / "output"))
    captured = Mock(return_value=Mock())
    monkeypatch.setattr("kajovo.studio.photos.photo_batch.new_job", captured)
    monkeypatch.setattr("kajovo.studio.photos.photo_batch.prepare_and_submit", Mock())
    monkeypatch.setattr(page, "execute", lambda title, function, receive=None: function(Mock(), Mock()))
    page.start()
    assert captured.call_args.kwargs["source_paths"] == [page.photos.item(1).data(Qt.UserRole)]
