"""Pokrytí posuvů a volba nativního backendu forenzního snímkování."""

from types import SimpleNamespace
from unittest.mock import patch

from scripts.render_studio import batch_fixture, capture_command_palette, native_platform, scroll_positions, source_inventory


def test_renderer_covers_long_content_without_gaps():
    positions = scroll_positions(2600, 420)
    assert positions[0] == 0
    assert positions[-1] == 2600
    assert all(right - left <= 388 for left, right in zip(positions, positions[1:], strict=False))
    assert scroll_positions(0, 420) == (0,)


def test_renderer_native_backend_matches_host():
    assert native_platform("darwin") == "cocoa"
    assert native_platform("win32") == "windows"
    assert native_platform("linux") == "xcb"


def test_renderer_source_inventory_skips_appledouble(tmp_path):
    studio = tmp_path / "kajovo" / "studio"
    studio.mkdir(parents=True)
    (studio / "._dialog.py").write_bytes(b"\x00\x05\x16\x07")
    (studio / "dialog.py").write_text("class Popup(QDialog):\n    pass\n", encoding="utf-8")
    result = source_inventory(tmp_path)
    assert len(result["modules"]) == 1
    assert result["modules"][0]["classes"][0]["name"] == "Popup"


def test_renderer_palette_captures_queries_without_activating_commands():
    queries = []
    opened = []
    hidden = []
    palette = SimpleNamespace(search=SimpleNamespace(setText=queries.append), hide=lambda: hidden.append(True))
    window = SimpleNamespace(command_palette=palette, open_command_palette=lambda: opened.append(True))
    captures = []
    capture_command_palette(window, lambda _widget, name: captures.append(name))
    capture_command_palette(window, lambda _widget, name: captures.append(name), concurrent=True)
    assert opened == [True, True]
    assert hidden == [True, True]
    assert queries == ["", "nastaveni", "nenalezitelny prikaz 938147", "", "mistni kontrola"]
    assert captures == ["command_palette", "command_palette_query", "command_palette_empty",
                        "command_palette_concurrent", "command_palette_active_operation"]


def test_renderer_batch_fixture_survives_page_activation(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel
    from kajovo.core.config import AppSettings
    from kajovo.studio.batches import BatchesPage
    from kajovo.studio.context import StudioContext

    context = StudioContext(AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")), None)
    page = BatchesPage(context)
    qtbot.addWidget(page)
    with patch.object(page, "_local_records", side_effect=lambda: batch_fixture(tmp_path)):
        page.page_activated()
        page.records[0]["remote"]["request_counts"]["completed"] = 0
        page.page_activated()
    assert page.records[0]["remote"]["request_counts"] == {"completed": 8, "failed": 1, "total": 12}
    assert page.records[0]["started_at"] == "2026-09-15T10:00:00+00:00"
    captions = [label.text() for label in page.card_widgets["batch_ukazka"].findChildren(QLabel)]
    assert any("9 z 12 úloh · 1 chyb" in text for text in captions)
    assert any("15.09.2026" in text for text in captions)
