"""Rychlý přechod zachovává zadání a otevírá pouze existující práci."""

import threading

from PySide6.QtCore import Qt

from kajovo.core.config import AppSettings
from kajovo.studio.application import create_window


def make_window(qtbot, tmp_path, monkeypatch, *, reduced_motion=False):
    monkeypatch.chdir(tmp_path)
    window = create_window(AppSettings(
        log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"),
        comic_library_dir=str(tmp_path / "comics"), ui_reduced_motion=reduced_motion,
    ))
    qtbot.addWidget(window)
    return window


def test_shortcut_navigates_without_starting_work_or_losing_input(qtbot, tmp_path, monkeypatch):
    window = make_window(qtbot, tmp_path, monkeypatch)
    window.workbench.prompt.setPlainText("Zachované rozpracované zadání")
    window.show()
    window.activateWindow()
    window.workbench.prompt.setFocus()
    qtbot.wait(30)
    qtbot.keyClick(window.workbench.prompt, Qt.Key_K, Qt.ControlModifier)
    qtbot.waitUntil(window.command_palette.isVisible)
    window.command_palette.search.setText("nastaveni")
    qtbot.keyClick(window.command_palette.search, Qt.Key_Return)
    assert window.heading.text() == "Nastavení"
    assert window.navigation["settings"].isChecked()
    assert window.workbench.prompt.toPlainText() == "Zachované rozpracované zadání"
    assert not window.operations.records
    assert not window.command_palette.isVisible()


def test_palette_opens_same_running_dialog_and_ignores_stale_ids(qtbot, tmp_path, monkeypatch):
    window = make_window(qtbot, tmp_path, monkeypatch)
    release = threading.Event()
    record = window.operations.start("Místní kontrola podkladů", lambda task: release.wait(10), popup=False)
    try:
        window.open_command_palette()
        window.command_palette.search.setText("Místní kontrola podkladů")
        qtbot.keyClick(window.command_palette.search, Qt.Key_Return)
        assert record.dialog.isVisible()
        assert len(window.operations.records) == 1
        window.execute_palette_command("operation:již-neexistuje")
        window.execute_palette_command("section:není-sekce")
        assert len(window.operations.records) == 1
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(record.terminal), timeout=10000)
    assert record.dialog is window.operations.records[record.identifier].dialog
    record.dialog.close()


def test_converter_inherits_and_updates_motion_preference(qtbot, tmp_path, monkeypatch):
    window = make_window(qtbot, tmp_path, monkeypatch, reduced_motion=True)
    window.execute_palette_command("tool:converter")
    qtbot.addWidget(window.converter)
    assert window.converter.operations.reduced_motion
    assert window.converter.mark.reduced_motion
    window.context.settings.ui_reduced_motion = False
    window.context.settings_changed.emit()
    assert not window.converter.operations.reduced_motion
    assert not window.converter.mark.reduced_motion


def test_running_converter_remains_reachable_from_palette_and_blocked_close(qtbot, tmp_path, monkeypatch):
    window = make_window(qtbot, tmp_path, monkeypatch)
    window.open_converter()
    converter = window.converter
    qtbot.addWidget(converter)
    release = threading.Event()
    record = converter.operations.start("Převod místního textu", lambda task: release.wait(10), popup=False)
    try:
        assert not window.close()
        assert converter.operations.overview is not None
        assert converter.operations.overview.isVisible()
        assert window.operations.overview is None
        window.open_command_palette()
        window.command_palette.search.setText("Převod místního textu")
        qtbot.keyClick(window.command_palette.search, Qt.Key_Return)
        assert record.dialog.isVisible()
        assert len(converter.operations.records) == 1
        assert not window.operations.records
        converter.operations.overview.hide()
        window.execute_palette_command("work:overview")
        assert converter.operations.overview.isVisible()
        assert window.operations.overview is None
        window.execute_palette_command("converter-operation:již-neexistuje")
        assert len(converter.operations.records) == 1
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(record.terminal), timeout=10000)
    record.dialog.close()
