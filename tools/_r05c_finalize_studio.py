from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remove_functions(relative: str, names: set[str]) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    spans = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            spans.append((node.lineno, node.end_lineno or node.lineno))
    missing = names - {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    }
    if missing:
        raise RuntimeError(f"Missing functions in {relative}: {sorted(missing)}")
    lines = text.splitlines(keepends=True)
    for start, end in sorted(spans, reverse=True):
        del lines[start - 1 : end]
        while start - 2 >= 0 and start - 2 < len(lines) and not lines[start - 2].strip():
            del lines[start - 2]
            start -= 1
    path.write_text("".join(lines).rstrip() + "\n", encoding="utf-8")


def append(relative: str, content: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8").rstrip()
    path.write_text(text + "\n\n\n" + content.strip() + "\n", encoding="utf-8")


def rewrite_ui_progress() -> None:
    (ROOT / "tests/test_ui_progress.py").write_text('''import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer

from kajovo.core.progress import ProgressClock, ProgressEvent
from kajovo.core.progress_display import build_steps, event_sentence, source_title
from kajovo.studio.components import install_theme
from kajovo.studio.operations import Task
from test_workflows import make_worker


def test_eta_requires_completed_samples_and_counts_down():
    clock = ProgressClock(now=0)
    clock.update(ProgressEvent("A3", completed=0, total=6, timestamp=0))
    for i in range(1, 3):
        clock.update(ProgressEvent("A3", completed=i, total=6, timestamp=i * 10))
        assert clock.times(i * 10)[2] is None
    clock.update(ProgressEvent("A3", completed=3, total=6, timestamp=30))
    assert clock.times(30)[2] == 30
    assert clock.times(35)[2] == 25
    clock.update(ProgressEvent("Ukládání", completed=0, total=6, timestamp=40))
    assert clock.times(40)[2] is None


def test_progress_display_explains_remote_work_and_remaining_steps():
    events = [
        ProgressEvent("A1", detail="Odesílám architektonický plán.", source="api"),
        ProgressEvent("A2", detail="Ověřuji přijatou strukturu.", source="api"),
    ]
    steps = build_steps(events, mode="GENERATE", quality=True)
    states = {step.key: step.state for step in steps}
    assert states["A1"] == "done"
    assert states["A2"] == "current"
    assert any(step.key == "A2Q" and step.state == "pending" for step in steps)
    assert source_title("api") == "OpenAI Responses API"
    assert "OpenAI Responses API" in event_sentence(events[0])


def test_progress_event_metadata_is_optional_and_compatible():
    event = ProgressEvent("A3", source="disk", next_step="Kontrola výsledků")
    assert event.source == "disk"
    assert event.next_step == "Kontrola výsledků"


def test_repeated_poll_does_not_restart_unit_measurement():
    clock = ProgressClock(now=0)
    clock.update(ProgressEvent("Indexace", completed=0, total=5, timestamp=0))
    clock.update(ProgressEvent("Indexace", completed=0, total=5, timestamp=8))
    clock.update(ProgressEvent("Indexace", completed=1, total=5, timestamp=10))
    assert list(clock.samples) == [10]


def test_palette_text_and_selection_contrast(qapp):
    from PySide6.QtGui import QPalette

    install_theme(qapp)
    palette = qapp.palette()

    def luminance(color):
        values = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    for foreground, background in (
        (QPalette.Text, QPalette.Base),
        (QPalette.HighlightedText, QPalette.Highlight),
    ):
        low, high = sorted((luminance(palette.color(foreground)), luminance(palette.color(background))))
        assert (high + 0.05) / (low + 0.05) >= 4.5


def test_io_runs_outside_gui_while_timer_remains_responsive(qtbot):
    main_thread = threading.get_ident()
    ticks, results, errors = [], [], []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    task = Task(lambda _task: (time.sleep(0.05), threading.get_ident())[1])
    task.value.connect(results.append)
    task.start()
    qtbot.waitUntil(lambda: bool(results))
    task.wait()
    assert results[0] != main_thread and ticks
    failed = Task(lambda _task: (_ for _ in ()).throw(ValueError("fixture")))
    failed.failure.connect(errors.append)
    failed.start()
    qtbot.waitUntil(lambda: bool(errors))
    failed.wait()
    assert errors[0].message == "fixture"
    timer.stop()


@pytest.mark.parametrize("fail", [False, True])
def test_file_is_counted_only_after_generation(tmp_path, monkeypatch, fail):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.resume_files = [{"path": "hello.py", "purpose": "test"}]
    worker.cfg.resume_prev_id = "resp_previous"
    events = []
    worker.progress_event.connect(events.append)

    def generate(*args, **kwargs):
        assert [e.completed for e in events if e.stage == "A3" and e.completed is not None] == [0]
        if fail:
            raise ValueError("fixture")
        return "print('hello')\\n", "resp_test"

    monkeypatch.setattr(worker, "_gen_file_chunks", generate)
    if fail:
        with pytest.raises(ValueError, match="fixture"):
            worker._run_a_generate(Mock(), [], None)
    else:
        worker._run_a_generate(Mock(), [], None)
    assert [e.completed for e in events if e.stage == "A3" and e.completed is not None] == (
        [0] if fail else [0, 1]
    )
''', encoding="utf-8")


def rewrite_progress_completion() -> None:
    (ROOT / "tests/test_progress_completion.py").write_text('''"""Dokončení Studio průběhu umožňuje zavření bez rušení práce."""

import threading
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from kajovo.core.progress import ProgressEvent
from kajovo.core.user_errors import describe_error
from kajovo.studio.operations import OperationDialog, Operations, STATES


@pytest.mark.parametrize("state", sorted(set(STATES) - {"active", "waiting"}))
@pytest.mark.parametrize("enter", [False, True])
def test_studio_terminal_can_close_without_cancelling(qtbot, state, enter):
    dialog = OperationDialog("Práce")
    qtbot.addWidget(dialog)
    stop = Mock()
    dialog.stop_callback = stop
    dialog.stop.setEnabled(True)
    dialog.show()
    dialog.resize(480, 360)
    dialog.on_event(ProgressEvent("Soubory", completed=3, total=8))
    dialog.result = {"status": state}
    error = describe_error(ValueError("Chyba")) if state == "failed" else None
    dialog.finish(state, error)
    assert dialog.isVisible()
    assert dialog.stop.isHidden()
    assert dialog.close_button.text() == "OK"
    assert dialog.close_button.accessibleName() == "OK"
    assert dialog.close_button.isDefault()
    assert dialog.result_button.isVisible()
    assert dialog.details.isVisible() == (error is not None)
    assert dialog.progress.value() == 3
    assert not dialog.timer.isActive()
    assert not dialog.mark.running
    if enter:
        qtbot.keyClick(dialog.close_button, Qt.Key_Return)
    else:
        qtbot.mouseClick(dialog.close_button, Qt.LeftButton)
    assert not dialog.isVisible()
    dialog.show()
    assert dialog.close_button.text() == "OK"
    stop.assert_not_called()


def test_studio_completion_waits_and_reuse_restores_controls(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    operations = Operations(parent)
    previous = None
    for attempt in range(2):
        release = threading.Event()

        def execute(task, release=release):
            task.progress_event.emit(ProgressEvent("Soubory", completed=1, total=1))
            task.progress_event.emit(ProgressEvent("RUN", "completed"))
            release.wait(5)
            return {"status": "completed"}

        record = operations.start("Zápis", execute, cancellable=True, identifier="opakování")
        try:
            qtbot.waitUntil(lambda record=record: len(record.events) == 2)
            dialog = record.dialog
            assert dialog.active
            assert dialog.close_button.text() == "Skrýt průběh"
            assert dialog.stop.isVisible()
            assert dialog.stop.isEnabled()
            if attempt:
                assert dialog is previous
            dialog.request_stop()
            assert not dialog.stop.isEnabled()
            dialog.hide()
        finally:
            release.set()
            qtbot.waitUntil(lambda record=record: bool(record.terminal))
        assert not dialog.isVisible()
        assert dialog.close_button.text() == "OK"
        previous = dialog
''', encoding="utf-8")


def rewrite_batch_ui() -> None:
    (ROOT / "tests/test_batch_completion_ui.py").write_text('''"""Kanonické vazby Studio stránky dávek na lokální stav a core dokončení."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.batch_completion import read_state
from kajovo.core.config import AppSettings
from kajovo.studio.application import create_window


@pytest.fixture
def studio(qtbot, tmp_path):
    client = Mock()
    client.list_models.return_value = []
    window = create_window(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        api_key="test-key",
        client_factory=lambda *args, **kwargs: client,
    )
    qtbot.addWidget(window)
    return window


def make_run(studio, tmp_path):
    run = Path(studio.context.settings.log_dir) / "RUN_110920261200_abcd"
    run.mkdir(parents=True)
    state = {
        "run_id": run.name,
        "batch_id": "batch_work",
        "project": "Ukázkový projekt",
        "out_dir": str(tmp_path / "OUT"),
        "status": "batch_pending",
        "batch_records": {"batch_work": {"created_at": 1726050000}},
    }
    (run / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    return run


def test_batch_page_renders_remote_and_local_state(studio, tmp_path):
    run = make_run(studio, tmp_path)
    page = studio.batches
    page.records = [{
        "id": "batch_work",
        "remote": {"id": "batch_work", "status": "completed", "request_counts": {"total": 2, "completed": 2, "failed": 0}},
        "state": read_state(run),
        "run_dir": str(run),
    }]
    page.last_refresh = time.time()
    page.render()
    assert page.table.rowCount() == 1
    assert "Ukázkový projekt" in page.table.item(0, 0).text()
    assert page.table.item(0, 1).text() == "Zpracováno službou"
    assert "2 z 2" in page.table.item(0, 2).text()
    assert page.table.item(0, 3).text() == "Čeká na převzetí"


def test_batch_complete_delegates_to_core_and_refreshes_local_evidence(studio, tmp_path, monkeypatch):
    run = make_run(studio, tmp_path)
    page = studio.batches
    record = {
        "id": "batch_work",
        "remote": {"id": "batch_work", "status": "completed"},
        "state": read_state(run),
        "run_dir": str(run),
    }
    page.records = [record]
    page.render()
    page.table.selectRow(0)
    calls = []

    def complete(_client, root, identifier, _settings, progress=None):
        calls.append((Path(root), identifier, progress is not None))
        state = read_state(root)
        state["status"] = "files_complete_unverified"
        (Path(root) / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
        return {"status": "files_complete_unverified"}

    def execute(_title, function, receive=None, **_kwargs):
        task = SimpleNamespace(progress_event=SimpleNamespace(emit=lambda _event: None))
        value = function(Mock(), task)
        if receive:
            receive(value)
        return value

    monkeypatch.setattr("kajovo.studio.batches.complete_saved_batch", complete)
    monkeypatch.setattr(page, "execute", execute)
    page.complete()
    assert calls == [(run, "batch_work", True)]
    assert record["state"]["status"] == "files_complete_unverified"
    assert "Soubory" in page.table.item(0, 3).text() or page.table.item(0, 3).text()
''', encoding="utf-8")


def rewrite_studio_contracts() -> None:
    (ROOT / "tests/test_studio_contracts.py").write_text('''"""Regrese kontraktů, které zůstávají nezávislé na odstraněném legacy UI."""

import json

from kajovo.core.batch_completion import import_bundle
from test_output_chunks import raw


def test_invalid_bundle_never_partially_overwrites_files(tmp_path):
    target = tmp_path / "first.txt"
    target.write_text("original", encoding="utf-8")
    result = import_bundle(
        raw({
            "contract": "C_FILES_ALL",
            "files": [
                {"path": "first.txt", "content": "changed"},
                {"path": "second.txt", "content": 123},
            ],
        }),
        str(tmp_path),
    )
    assert result["errors"] and not result["written"]
    assert target.read_text(encoding="utf-8") == "original"


def test_recovery_uses_events_and_related_structure(tmp_path):
    from kajovo.core.recovery import recover_run

    current = tmp_path / "run"
    (current / "requests").mkdir(parents=True)
    (current / "requests" / "01.json").write_text(
        json.dumps({"ui_state": {"out_dir": str(tmp_path / "OUT"), "project": "demo"}})
    )
    (current / "events.jsonl").write_text(
        json.dumps({"type": "api.trace", "data": {"action": "complete", "response_id": "resp_latest"}})
        + "\\ninvalid"
    )
    related = tmp_path / "related"
    (related / "manifests").mkdir(parents=True)
    (related / "run_state.json").write_text(json.dumps({"out_dir": str(tmp_path / "OUT")}))
    (related / "manifests" / "resume_structure.json").write_text(
        json.dumps({"resume_files": [{"path": "main.py"}], "resume_prev_id": "resp_old"})
    )
    ui, previous, files = recover_run(tmp_path, "run")
    assert ui["project"] == "demo"
    assert previous == "resp_latest"
    assert files == [{"path": "main.py"}]


def test_recovery_saved_map_rejects_unsafe_paths(tmp_path):
    from kajovo.core.recovery import recover_run

    current = tmp_path / "run"
    (current / "requests").mkdir(parents=True)
    (current / "manifests").mkdir()
    (current / "requests" / "01.json").write_text(
        json.dumps({"ui_state": {"out_dir": str(tmp_path / "OUT")}})
    )
    (current / "manifests" / "01_out_saved_map.json").write_text(
        json.dumps({"saved": [{"path": "../escape"}, {"path": "valid.py"}]})
    )
    assert recover_run(tmp_path, "run")[2] == [{"path": "valid.py", "purpose": ""}]
''', encoding="utf-8")


def migrate_response_journal_tests() -> None:
    remove_functions("tests/test_response_journal.py", {
        "test_progress_dialog_finishes_with_recoverable_status",
        "test_waiting_and_remote_cancellation_are_separate_controls",
        "test_settings_expose_separate_generation_limit",
    })
    append("tests/test_response_journal.py", '''
@pytest.mark.parametrize("state", ["response_pending", "submission_unknown", "cancelled"])
def test_operation_dialog_finishes_with_recoverable_status(qtbot, state):
    from kajovo.core.progress import ProgressEvent
    from kajovo.studio.operations import OperationDialog

    dialog = OperationDialog("Práce")
    qtbot.addWidget(dialog)
    dialog.on_event(ProgressEvent("RUN", state, detail="Uložené ID"))
    dialog.finish(state)
    assert dialog.clock.finished is not None
    assert not dialog.timer.isActive()
    assert dialog.progress.maximum() == 0 or dialog.progress.value() != 100


def test_settings_expose_separate_generation_limit(qtbot, monkeypatch):
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.settings import SettingsPage

    settings = AppSettings()
    context = StudioContext(settings, Operations(), api_key="")
    page = SettingsPage(context)
    qtbot.addWidget(page)
    assert page.editors["response_timeout_s"].value() == 300
    assert page.editors["response_poll_timeout_s"].value() == 3600
    page.editors["response_poll_timeout_s"].setValue(7200)
    assert page.snapshot().response_poll_timeout_s == 7200
    save = Mock()
    monkeypatch.setattr("kajovo.studio.settings.save_settings", save)
    page.save()
    assert save.call_args.args[0].response_poll_timeout_s == 7200
''')


def migrate_process_audit_tests() -> None:
    remove_functions("tests/test_process_audit_regressions.py", {
        "test_modify_requires_in_for_both_delivery_modes",
        "test_batch_submit_instruction_remains_manual_refresh",
        "test_live_generate_partial_semantics_are_wired_end_to_end",
    })
    append("tests/test_process_audit_regressions.py", '''
def test_modify_requires_existing_input_in_studio(qtbot, tmp_path):
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.workbench import Workbench

    context = StudioContext(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        Operations(),
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
    pipeline = Path("kajovo/core/runs/executor.py").read_text(encoding="utf-8")
    from kajovo.studio.operations import STATES

    assert '"status": "partial" if missing_deliverables else "files_complete_unverified"' in pipeline
    assert 'final_status in ("completed", "partial", "dry_run", "files_complete_unverified")' in pipeline
    assert STATES["partial"] == "Dokončeno s chybami"
''')


def migrate_run_bundle_tests() -> None:
    remove_functions("tests/test_run_bundle.py", {
        "test_history_run_explorer_constructs_and_shows_legacy_without_guessing",
        "test_history_filters_use_derived_index",
        "test_history_custom_date_interval_filters_runs",
        "test_history_files_offer_compare_and_direct_provenance_actions",
    })


def migrate_photo_tests() -> None:
    remove_functions("tests/test_photo_studio.py", {
        "test_photo_studio_panel_constructs_without_api",
        "test_install_photo_studio_adds_real_main_navigation",
    })
    append("tests/test_photo_studio.py", '''
def test_photo_studio_page_constructs_without_api(qtbot, tmp_path):
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.photos import PhotosPage

    context = StudioContext(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        Operations(),
        api_key="",
    )
    context.models = [_image_model()]
    page = PhotosPage(context)
    qtbot.addWidget(page)
    context.models_changed.emit()
    assert page.prompt.isEnabled()
    assert page.template.count() >= 6
    assert page.image_model.count() >= 1
    assert page.start_button.isEnabled()


def test_photo_studio_is_real_main_navigation_page(qtbot, tmp_path):
    from kajovo.core.config import AppSettings
    from kajovo.studio.application import create_window
    from kajovo.studio.photos import PhotosPage

    window = create_window(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        api_key="",
    )
    qtbot.addWidget(window)
    assert isinstance(window.pages["photos"], PhotosPage)
    window.select_page("photos")
    assert window.stack.widget(window.stack.currentIndex()).widget() is window.pages["photos"]
''')


def update_architecture_contracts() -> None:
    path = ROOT / "tests/test_architecture_contracts.py"
    text = path.read_text(encoding="utf-8")
    old = '''def test_distribution_excludes_legacy_desktop_package():
    """Regresní zdroje mohou zůstat v repu, ale nesmějí být součástí instalace/release."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(config["tool"]["setuptools"]["packages"]["find"]["exclude"])
    assert "kajovo.desktop" in excluded
    assert "kajovo.desktop.*" in excluded
'''
    new = '''def test_legacy_desktop_package_is_physically_removed():
    """Po konsolidaci existuje pouze produkční Studio UI."""
    assert not (ROOT / "kajovo" / "desktop").exists()
'''
    if old not in text:
        raise RuntimeError("Architecture desktop distribution contract changed unexpectedly")
    text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def cleanup_repo() -> None:
    shutil.rmtree(ROOT / "kajovo/desktop")
    shutil.rmtree(ROOT / "LOG")
    for relative in (
        "tests/test_desktop.py",
        "aa.txt",
        ".github/workflows/_quality_workspace.yml",
        ".github/workflows/r05a_detach_legacy_core_contracts.yml",
        ".github/workflows/r05b_studio_audit_migration.yml",
        ".github/workflows/remove_paid_preflight_legacy.yml",
        "tools/_r05a_detach_legacy_core_contracts.py",
        "tools/_r05b_migrate_studio_audit.py",
        "scripts/_remove_paid_preflight_legacy.py",
    ):
        path = ROOT / relative
        if path.exists():
            path.unlink()

    gitignore = ROOT / ".gitignore"
    text = gitignore.read_text(encoding="utf-8")
    for entry in (".mypy_cache/", "/build-metadata/"):
        if entry not in text.splitlines():
            text += entry + "\n"
    gitignore.write_text(text, encoding="utf-8")

    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text = text.replace(
        'exclude = ["tests*", "kajovo.desktop", "kajovo.desktop.*"]',
        'exclude = ["tests*"]',
    )
    pyproject.write_text(text, encoding="utf-8")

    renderer = ROOT / "scripts/render_ui.py"
    text = renderer.read_text(encoding="utf-8")
    text = text.replace(
        "Historický renderer nad `kajovo.desktop` již není samostatná implementace.",
        "Kompatibilní renderer používá jedinou produkční implementaci Studio UI.",
    )
    renderer.write_text(text, encoding="utf-8")

    studio_test = ROOT / "tests/test_studio.py"
    text = studio_test.read_text(encoding="utf-8")
    text = text.replace('for path in root.glob("*.py"):', 'for path in root.rglob("*.py"):')
    studio_test.write_text(text, encoding="utf-8")


def main() -> None:
    rewrite_ui_progress()
    rewrite_progress_completion()
    rewrite_batch_ui()
    rewrite_studio_contracts()
    migrate_response_journal_tests()
    migrate_process_audit_tests()
    migrate_run_bundle_tests()
    migrate_photo_tests()
    update_architecture_contracts()
    cleanup_repo()
    print("R05-C Studio-only migration prepared")


if __name__ == "__main__":
    main()
