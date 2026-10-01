"""Historie neoznamuje převzetí souborů, které jsou zatím pouze ve stagingu."""
from kajovo.studio.history import HistoryPage
from test_qa_qfile_http_closure import WorkflowHttp, page_fixture, settle


def test_qfile_history_staging_label_does_not_claim_publication(qtbot, monkeypatch, tmp_path):
    transport = WorkflowHttp(tmp_path)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, "QFILE", transport)
    page.start_button.click()
    settle(qtbot, operations, page)
    page.start_button.click()
    settle(qtbot, operations, page)
    record = list(operations.records.values())[-1]
    assert record.result["status"] == "files_complete_unverified"
    assert not (tmp_path / "OUT/výsledek/navrh.md").exists()
    staged = record.result["saved"]["staged"]
    assert len(staged) == 1
    assert (tmp_path / "LOG" / record.identifier / staged[0]["staged_path"]).read_bytes() == "# Výsledek\nPřesné bytes.\n".encode()
    history = HistoryPage(page.context, page, page)
    qtbot.addWidget(history)
    history.refresh()
    qtbot.waitUntil(lambda: history.model.rowCount() > 0 and not operations.active, timeout=30000)
    run = next(history.model.run_at(i) for i in range(history.model.rowCount())
               if history.model.run_at(i).run_id == record.identifier)
    assert run.status.key == "files_complete_unverified"
    assert run.status.label == "Soubory připravené, funkčnost neověřena"
