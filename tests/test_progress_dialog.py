from __future__ import annotations

import json

from PySide6.QtWidgets import QApplication

from kajovospend.ui.dialogs.forms import TaskProgressDialog


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def test_progress_dialog_renders_percent_eta_and_detail() -> None:
    _app()
    dialog = TaskProgressDialog(title='Import')
    dialog.update_progress(2, 5, json.dumps({'label': 'Import dokumentů', 'detail': 'Čtu soubor z disku', 'step': 'document_load', 'percent': 40, 'eta_seconds': 75}, ensure_ascii=False))
    assert dialog.percent_label.text() == '40 %'
    assert '01:15' in dialog.eta_label.text()
    assert 'Čtu soubor z disku' in dialog.detail_label.text()
    assert 'document load' in dialog.step_label.text()
