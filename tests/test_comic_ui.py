"""Qt kontrakty atomických tokenů, sazby a skutečných příkazů knihovny."""
from dataclasses import asdict
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from kajovo.core.comic_types import ComicError, PanelFormat
from kajovo.core.config import AppSettings
from kajovo.studio.application import create_window
from kajovo.studio.comic_editor import EntityPromptEdit, render_panel
from test_comic_domain import WorkingClient, png


@pytest.fixture
def comic_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = AppSettings(comic_library_dir=str(tmp_path / "comics"), log_dir=str(tmp_path / "log"))
    window = create_window(settings, api_key="", client_factory=Mock())
    qtbot.addWidget(window)
    window.comics.timer.stop()
    window.show()
    return window


def test_atomic_chip_delete_undo_copy_and_roundtrip(qtbot):
    editor = EntityPromptEdit()
    qtbot.addWidget(editor)
    entity = {"id": "a" * 32, "kind": "character", "name": "Karel"}
    editor.insertPlainText("Vedle ")
    editor.insert_entity(entity)
    editor.show()
    qtbot.waitUntil(lambda: editor.cursorRect().x() > 60)
    document = editor.prompt_document()
    assert document["nodes"][-1] == {"type": "character_ref", "entity_id": entity["id"]}
    qtbot.keyClick(editor, Qt.Key_Backspace)
    assert editor.toPlainText() == "Vedle "
    editor.undo()
    assert editor.prompt_document() == document
    editor.selectAll()
    mime = editor.createMimeDataFromSelection()
    target = EntityPromptEdit()
    qtbot.addWidget(target)
    target.entities = {entity["id"]: entity}
    target.insertFromMimeData(mime)
    assert target.prompt_document() == document
    target.load_document(document, [entity])
    assert target.prompt_document() == document


def test_plain_text_never_becomes_entity(qtbot):
    editor = EntityPromptEdit()
    qtbot.addWidget(editor)
    editor.setPlainText("[Karel] @character_123")
    assert editor.prompt_document()["nodes"] == [{"type": "text", "text": "[Karel] @character_123"}]


def test_chip_selection_replacement_is_atomic(qtbot):
    editor = EntityPromptEdit()
    qtbot.addWidget(editor)
    editor.insert_entity({"id": "b" * 32, "kind": "environment", "name": "Pokoj 203"})
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.PreviousCharacter, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.insertPlainText("jiné místo")
    assert editor.prompt_document()["nodes"] == [{"type": "text", "text": "jiné místo"}]


def test_studio_comic_crud_and_panel_save(comic_window):
    page = comic_window.comics
    assert "comics" in comic_window.pages
    project = page.service.store.project("Český komiks")
    page.project_id = project
    page.refresh_projects()
    page.add_panel()
    page.prompt.insertPlainText("Večer na ulici")
    page.width_px.setValue(731)
    page.height_px.setValue(987)
    assert page.save_panel()
    record = page.service.store.get("panels", page.panel_id)
    assert record["format"]["width"] == 731
    assert page.service.store.get("prompts", record["prompt_id"])["document"]["nodes"][0]["text"] == "Večer na ulici"
    page.panel_action("duplicate")
    assert page.panels.count() == 2
    assert page.format() == PanelFormat(731, 987)
    assert asdict(page.format()) == page.panel_record["format"]


def test_text_overlay_renders_and_rejects_overflow(qapp):
    layer = {"kind": "caption", "text": "Příliš žluťoučký kůň", "x": .1, "y": .1,
             "w": .8, "h": .3, "tail_x": .5, "tail_y": .5, "font_size": .03}
    image = render_panel(png(), [layer])
    assert image.width() == 1024
    assert image.pixelColor(110, 110).name() == "#ffffff"
    layer["text"] = "Slovo " * 200
    layer["w"] = .1
    layer["h"] = .1
    with pytest.raises(ComicError, match="nevejde"):
        render_panel(png(), [layer])


def test_export_text_has_dark_ink_under_studio_theme(comic_window):
    layer = {"kind": "caption", "text": "Český text", "x": .1, "y": .1,
             "w": .8, "h": .3, "tail_x": .5, "tail_y": .5, "font_size": .03}
    image = render_panel(png(), [layer])
    dark = sum(image.pixelColor(x, y).lightness() < 50 for x in range(150, 400) for y in range(130, 185))
    assert dark > 100, "Tmavý motiv aplikace nesmí změnit inkoust na bílý."


def test_paper_dimensions_use_dpi():
    assert PanelFormat.paper("A4").width == 2480
    assert PanelFormat.paper("A4").height == 3508
    assert PanelFormat.paper("DL", True).width == 2598


def test_switching_project_preserves_unsaved_style(comic_window):
    page = comic_window.comics
    first = page.service.store.project("První")
    second = page.service.store.project("Druhý")
    page.project_id = first
    page.refresh_projects()
    page.style_fields["description"].setText("Inkoustová kresba")
    page.projects.setCurrentIndex(page.projects.findData(second))
    assert page.service.store.get("projects", first)["style"]["description"] == "Inkoustová kresba"
    assert page.projects.currentText() == "Druhý"
    assert page.project_id == second


def test_library_setting_changes_namespace_safely(comic_window, tmp_path):
    page = comic_window.comics
    project = page.service.store.project("Původní knihovna")
    page.project_id = project
    page.refresh_projects()
    comic_window.context.settings.comic_library_dir = str(tmp_path / "new-library")
    page.settings_changed()
    assert page.project_id is None
    assert page.projects.count() == 1
    assert page.service.store.root == tmp_path / "new-library"


def test_window_close_persists_pending_comic(comic_window):
    page = comic_window.comics
    project = page.service.store.project("Před zavřením")
    page.project_id = project
    page.refresh_projects()
    page.add_panel()
    panel = page.panel_id
    page.prompt.insertPlainText("Rozpracovaná scéna")
    page.style_fields["description"].setText("Ruční inkoust")
    comic_window.close()
    record = page.service.store.get("panels", panel)
    assert page.service.store.get("prompts", record["prompt_id"])["document"]["nodes"][0]["text"] == "Rozpracovaná scéna"
    assert page.service.store.get("projects", project)["style"]["description"] == "Ruční inkoust"


def test_desktop_end_to_end_with_isolated_provider(comic_window, qtbot, tmp_path, monkeypatch):
    page = comic_window.comics
    client = WorkingClient()
    comic_window.context.api_key = "test-only"
    comic_window.context.client_factory = lambda *args, **kwargs: client
    project = page.service.store.project("Pracovní scénář")
    page.project_id = project
    page.refresh_projects()
    page.generate_bible()
    qtbot.waitUntil(lambda: bool(page.service.store.get("projects", project)["bible_id"]), timeout=30000)
    qtbot.waitUntil(lambda: not page.running, timeout=10000)
    reference = tmp_path / "reference.png"
    reference.write_bytes(png())
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Karel", True))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *args, **kwargs: ("Modrý oděv", True))
    monkeypatch.setattr(page, "files", lambda: [str(reference)])
    page.add_entity("character")
    entity = page.service.store.rows("entities")[0]["id"]
    qtbot.waitUntil(lambda: len(page.service.store.references(project, entity)) == 2, timeout=10000)
    qtbot.waitUntil(lambda: all(r.terminal for r in comic_window.operations.records.values()), timeout=10000)
    page.refresh_entities()
    page.entity_lists["character"].setCurrentRow(0)
    page.generate_entity("character")
    qtbot.waitUntil(lambda: bool(page.service.store.get("entities", entity)["active_revision"]), timeout=30000)
    qtbot.waitUntil(lambda: not page.running, timeout=10000)
    page.add_panel()
    page.prompt.insertPlainText("U okna stojí ")
    page.tokens.setCurrentIndex(page.tokens.findData(entity))
    page.insert_token()
    page.width_px.setValue(731)
    page.height_px.setValue(987)
    page.generate_panels()
    qtbot.waitUntil(lambda: client.submits == 1, timeout=30000)
    qtbot.waitUntil(lambda: not page.running, timeout=10000)
    client.complete("batch_1")
    operation = page.service.store.rows("operations", "kind='panels'")[0]["id"]
    page.execute_operation(operation)
    qtbot.waitUntil(lambda: bool(page.service.store.get("panels", page.panel_id)["active_version"]), timeout=30000)
    qtbot.waitUntil(lambda: not page.running, timeout=10000)
    page.refresh_panels()
    assert page.overlays.image.size().width() == 731
    assert page.versions.count() == 1
    assert page.prompt.prompt_document()["nodes"][-1]["entity_id"] == entity
    page.overlays.add_layer()
    page.overlays.text.setPlainText("Dobrý večer!")
    from PySide6.QtWidgets import QFileDialog
    destination = tmp_path / "export.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), "PNG (*.png)"))
    page.export_panel()
    assert destination.is_file(), page.notice.text()
    assert page.versions.count() == 2
    from PySide6.QtGui import QImage
    exported = QImage(str(destination))
    assert (exported.width(), exported.height()) == (731, 987)
