"""Import zadání musí při vadných datech zachovat předchozí formulář."""
import json

import pytest
from PySide6.QtWidgets import QPushButton

from test_qa_qfile_http_closure import WorkflowHttp, page_fixture


@pytest.mark.parametrize('patch', [{'temperature':{'invalid':1}}, {'temperature':float('inf')}, {'attached_file_ids':{'file_1':'foreign'}}, {'attached_vector_store_ids':[None]}])
def test_import_invalid_field_keeps_previous_form_and_signals(qtbot, monkeypatch, tmp_path, patch):
    transport = WorkflowHttp(tmp_path)
    page, operations = page_fixture(qtbot,monkeypatch,tmp_path,'QA',transport)
    before = page.state(secrets=True)
    source = tmp_path/'invalid.json'
    source.write_text(json.dumps({'project':'Cizí projekt', 'prompt':'Cizí zadání', **patch}))
    monkeypatch.setattr('kajovo.studio.workbench.get_open_file_name',lambda *a:(str(source),''))
    page.findChild(QPushButton,'run.load').click()
    assert page.state(secrets=True) == before
    assert not page.widgets['temperature'].signalsBlocked()
    assert not operations.records and transport.calls == []
