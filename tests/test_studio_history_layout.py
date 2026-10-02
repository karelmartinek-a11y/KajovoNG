"""Filtry historie zůstávají čitelné v úzkém i běžném okně studia."""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QBoxLayout, QPushButton

from kajovo.core.config import AppSettings
from kajovo.studio.application import create_window


@pytest.mark.parametrize("width,search_width,project_width", [(640, 300, 180), (1440, 250, 160)])
def test_history_filters_have_useful_width_and_all_toolbar_actions_fit(qtbot, tmp_path, monkeypatch,
                                                                     width, search_width, project_width):
    monkeypatch.chdir(tmp_path)
    window = create_window(AppSettings(
        log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"),
        comic_library_dir=str(tmp_path / "comics"),
    ), api_key="")
    qtbot.addWidget(window)
    page = window.history
    monkeypatch.setattr(page, "refresh", lambda: None)
    window.select_page("history")
    window.resize(width, 700)
    window.show()
    qtbot.waitUntil(lambda: page.search.width() >= search_width)
    assert window.width() == width
    assert page.project_filter.width() >= project_width
    expected = QBoxLayout.TopToBottom if width == 640 else QBoxLayout.LeftToRight
    assert page._filter_layout.direction() == expected
    assert page._timeline_layout.direction() == expected
    controls = [page.search, page.project_filter, page.mode_filter, page.status_filter, page.only_errors]
    for identifier in ("history.refresh", "history.filters.advanced", "history.filters.reset",
                       "history.zoom.out", "history.zoom.in", "history.zoom.fit"):
        controls.append(page.findChild(QPushButton, identifier))
    for control in controls:
        assert control is not None and control.isVisible()
        left = control.mapTo(page, QPoint(0, 0)).x()
        assert 0 <= left and left + control.width() <= page.width()
    assert page.project_filter.accessibleName()


def test_history_resizing_preserves_filter_values_and_returns_to_single_row(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    window = create_window(AppSettings(
        log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"),
        comic_library_dir=str(tmp_path / "comics"),
    ), api_key="")
    qtbot.addWidget(window)
    page = window.history
    monkeypatch.setattr(page, "refresh", lambda: None)
    monkeypatch.setattr(page, "apply_filters", lambda: None)
    window.select_page("history")
    window.show()
    page.search.setText("Rozpracovaný běh")
    page.project_filter.setText("Projekt Alfa")
    page.mode_filter.setCurrentIndex(3)
    page.filter_timer.stop()
    for width, direction in ((640, QBoxLayout.TopToBottom), (1440, QBoxLayout.LeftToRight)):
        window.resize(width, 700)
        qtbot.waitUntil(lambda expected=direction: page._filter_layout.direction() == expected)
        assert page.search.text() == "Rozpracovaný běh"
        assert page.project_filter.text() == "Projekt Alfa"
        assert page.mode_filter.currentData() == "QA"
