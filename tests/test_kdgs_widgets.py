from __future__ import annotations

import os

from PySide6.QtWidgets import QApplication, QLabel

from kajovospend.ui.design_contract import StateName
from kajovospend.ui.widgets.previews import DocumentPreviewWidget, RegionSelectorWidget
from kajovospend.ui.widgets.primitives import StateHost


def _app() -> QApplication:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance()
    return app or QApplication([])


def test_state_host_supports_all_required_states() -> None:
    _app()
    host = StateHost(default_title='Test', default_description='Test')
    host.set_content(QLabel('obsah'))
    for state in [StateName.DEFAULT, StateName.LOADING, StateName.EMPTY, StateName.ERROR, StateName.OFFLINE, StateName.MAINTENANCE, StateName.FALLBACK]:
        host.set_state(state)
        assert host.current_state == state


def test_preview_widgets_do_not_enforce_desktop_only_minimums() -> None:
    _app()
    document_preview = DocumentPreviewWidget()
    region_preview = RegionSelectorWidget()
    assert document_preview.widget().minimumWidth() <= 240
    assert document_preview.widget().minimumHeight() <= 180
    assert region_preview.minimumWidth() <= 280
    assert region_preview.minimumHeight() <= 240
