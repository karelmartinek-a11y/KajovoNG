"""Přístupná navigace mění skutečný panel a zachovává jediný aktivní výběr."""
from PySide6.QtGui import QAccessible, QAccessibleActionInterface

from kajovo.core.config import AppSettings
from kajovo.studio.application import create_window


def test_accessible_toggle_selects_actual_page_and_cannot_deselect_current(qtbot, tmp_path):
    window = create_window(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        api_key="",
    )
    qtbot.addWidget(window)
    history = window.navigation["history"]
    interface = QAccessible.queryAccessibleInterface(history).actionInterface()
    assert QAccessibleActionInterface.toggleAction() in interface.actionNames()
    interface.doAction(QAccessibleActionInterface.toggleAction())
    qtbot.waitUntil(lambda: not window.operations.active, timeout=10000)
    assert window.heading.text() == "Historie"
    assert window.stack.currentWidget().widget() is window.history
    assert [key for key, button in window.navigation.items() if button.isChecked()] == ["history"]
    interface.doAction(QAccessibleActionInterface.toggleAction())
    assert history.isChecked()
    assert window.heading.text() == "Historie"
    window.navigation["run"].click()
    assert window.stack.currentWidget() is window.workbench
    assert [key for key, button in window.navigation.items() if button.isChecked()] == ["run"]
