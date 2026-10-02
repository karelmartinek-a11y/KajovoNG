"""Rychlé otevření nabízí navigaci až po výslovném potvrzení uživatele."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QFontMetrics
from PySide6.QtWidgets import QDialog, QStyleOptionViewItem

from kajovo.studio.command_palette import CommandPalette, PaletteCommand


@pytest.fixture
def commands():
    return [
        PaletteCommand("run", "Zadání", "Připravit novou práci"),
        PaletteCommand("photos", "Fotografie", "Hromadné úpravy fotografií", ("obrázky",)),
        PaletteCommand("models", "Výběr modelu", "Prohlédnout dostupné modely"),
        PaletteCommand("converter", "Převod textů", "Záloha a převod do UTF-8", category="Nástroj"),
        PaletteCommand("overview", "Přehled probíhající práce", "Otevřít již spuštěné úlohy", category="Práce"),
    ]


def make_palette(qtbot, commands):
    palette = CommandPalette(commands)
    qtbot.addWidget(palette)
    palette.open()
    qtbot.waitUntil(palette.search.hasFocus)
    return palette


def test_search_matches_diacritics_multiple_terms_and_keywords_without_activation(qtbot, commands):
    palette = make_palette(qtbot, commands)
    selected = []
    palette.command_selected.connect(selected.append)
    palette.search.setText("VYBER   MODELU")
    assert palette.results.count() == 1
    assert palette.results.currentItem().data(Qt.UserRole) == "models"
    palette.search.setText("obrazky")
    assert palette.results.currentItem().data(Qt.UserRole) == "photos"
    palette.search.setText("nastroj utf")
    assert palette.results.currentItem().data(Qt.UserRole) == "converter"
    assert selected == []


def test_arrow_keys_preserve_typing_focus_and_enter_opens_exactly_once(qtbot, commands):
    palette = make_palette(qtbot, commands)
    selected = []
    palette.command_selected.connect(selected.append)
    qtbot.keyClick(palette.search, Qt.Key_Down)
    qtbot.keyClick(palette.search, Qt.Key_Down)
    qtbot.keyClick(palette.search, Qt.Key_Up)
    assert palette.search.hasFocus()
    assert palette.results.currentItem().data(Qt.UserRole) == "photos"
    qtbot.keyClick(palette.search, Qt.Key_Return)
    assert selected == ["photos"]
    assert not palette.isVisible()
    assert palette.result() == QDialog.DialogCode.Accepted
    assert not palette.open_button.isDefault()
    assert not palette.open_button.autoDefault()


def test_empty_result_and_escape_never_open_a_command(qtbot, commands):
    palette = make_palette(qtbot, commands)
    selected = []
    palette.command_selected.connect(selected.append)
    palette.search.setText("nenalezitelna-sekce")
    assert palette.results.count() == 0
    assert palette.empty.isVisible()
    assert not palette.open_button.isEnabled()
    qtbot.keyClick(palette.search, Qt.Key_Return)
    assert palette.isVisible()
    assert selected == []
    qtbot.keyClick(palette.search, Qt.Key_Escape)
    assert not palette.isVisible()
    assert selected == []


def test_new_query_selects_best_title_match_instead_of_previous_description_match(qtbot, commands):
    commands.insert(0, PaletteCommand("help", "Nápověda", "Jak otevřít přehled probíhající práce"))
    palette = make_palette(qtbot, commands)
    assert palette.results.currentItem().data(Qt.UserRole) == "help"
    palette.search.setText("prehled")
    assert palette.results.item(0).data(Qt.UserRole) == "overview"
    assert palette.results.currentItem().data(Qt.UserRole) == "overview"


def test_double_click_opens_clicked_item_without_single_click_activation(qtbot, commands):
    palette = make_palette(qtbot, commands)
    selected = []
    palette.command_selected.connect(selected.append)
    item = palette.results.item(1)
    position = palette.results.visualItemRect(item).center()
    qtbot.mouseClick(palette.results.viewport(), Qt.LeftButton, pos=position)
    assert selected == []
    qtbot.mouseDClick(palette.results.viewport(), Qt.LeftButton, pos=position)
    assert selected == ["photos"]
    assert not palette.isVisible()


def test_named_button_opens_current_result_and_reopening_resets_selection(qtbot, commands):
    palette = make_palette(qtbot, commands)
    selected = []
    palette.command_selected.connect(selected.append)
    palette.search.setText("prevod")
    assert QAccessible.queryAccessibleInterface(palette.open_button).text(QAccessible.Name) == "Otevřít vybranou položku"
    qtbot.mouseClick(palette.open_button, Qt.LeftButton)
    assert selected == ["converter"]
    palette.open()
    qtbot.waitUntil(palette.search.hasFocus)
    assert palette.search.text() == ""
    assert palette.results.count() == len(commands)
    assert palette.results.currentItem().data(Qt.UserRole) == "run"
    assert palette.search.hasFocus()


def test_replacing_running_commands_preserves_only_valid_selection(qtbot, commands):
    palette = make_palette(qtbot, commands)
    palette.results.setCurrentRow(4)
    selected = []
    palette.command_selected.connect(selected.append)
    palette.set_commands(commands[:2])
    assert palette.results.currentItem().data(Qt.UserRole) == "run"
    assert palette.results.count() == 2
    assert selected == []
    with pytest.raises(ValueError, match="identifikátor"):
        palette.set_commands([commands[0], commands[0]])
    assert palette.results.count() == 2


def test_small_palette_wraps_long_titles_and_can_scroll_to_last_result(qtbot):
    long_title = "Otevřít průběh právě probíhajícího zpracování rozsáhlého fotografického projektu"
    commands = [PaletteCommand(str(index), long_title, "Zobrazit stav již spuštěné práce bez nové operace.")
                for index in range(20)]
    palette = make_palette(qtbot, commands)
    palette.resize(340, 300)
    qtbot.waitUntil(lambda: palette.results.verticalScrollBar().maximum() > 0)
    assert not palette.results.horizontalScrollBar().isVisible()
    delegate = palette.results.itemDelegate()
    index = palette.results.model().index(0, 0)
    narrow_height = delegate.sizeHint(QStyleOptionViewItem(), index).height()
    minimum_text_height = QFontMetrics(delegate.title_font).boundingRect(
        0, 0, palette.results.viewport().width() - 112, 10000, Qt.TextWordWrap, long_title,
    ).height()
    assert narrow_height >= minimum_text_height + 34
    for _ in range(19):
        qtbot.keyClick(palette.search, Qt.Key_Down)
    assert palette.results.currentRow() == 19
    assert palette.results.verticalScrollBar().value() > 0
    palette.resize(680, 620)
    qtbot.waitUntil(lambda: delegate.sizeHint(QStyleOptionViewItem(), index).height() < narrow_height)
    assert palette.open_button.geometry().bottom() <= palette.height()
    assert palette.close_button.geometry().right() <= palette.width()


def test_long_project_token_wraps_every_character_inside_result_width(qtbot):
    title = "Práce na projektu · " + "PhotogrammetryProject_" * 8
    command = PaletteCommand("operation:long-project", title, "Otevřít průběh této úlohy")
    palette = make_palette(qtbot, [command])
    palette.resize(340, 300)
    qtbot.waitUntil(lambda: palette.results.verticalScrollBar().maximum() > 0)
    delegate = palette.results.itemDelegate()
    row_width = palette.results.viewport().width() - 2 * palette.results.spacing() - 4
    (layout, title_height), (_, description_height) = delegate._layouts(command, row_width)
    assert layout.lineCount() > 2
    for index in range(layout.lineCount()):
        line = layout.lineAt(index)
        assert line.naturalTextWidth() <= row_width - 100
    last = layout.lineAt(layout.lineCount() - 1)
    assert last.textStart() + last.textLength() == len(title)
    height = delegate.sizeHint(QStyleOptionViewItem(), palette.results.model().index(0, 0)).height()
    assert height >= title_height + description_height + 34
    palette.results.verticalScrollBar().setValue(palette.results.verticalScrollBar().maximum())
    assert palette.results.verticalScrollBar().value() > 0
