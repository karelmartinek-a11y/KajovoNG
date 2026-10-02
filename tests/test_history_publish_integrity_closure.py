"""Skutečná akce převzetí nesmí znovu zapečetit cizí nebo změněný bundle."""
import json

import pytest
from PySide6.QtCore import Qt

from kajovo.core.run_bundle import RunBundle
from kajovo.studio.history import HistoryPage
from test_qa_qfile_http_closure import WorkflowHttp, page_fixture, settle


@pytest.mark.parametrize("damage", ["foreign_metadata", "missing_manifest", "changed_state"])
def test_actual_history_publish_rejects_corrupt_source_without_disk_mutation(qtbot, monkeypatch, tmp_path, damage):
    transport = WorkflowHttp(tmp_path)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, "QFILE", transport)
    page.start_button.click()
    settle(qtbot, operations, page)
    page.start_button.click()
    settle(qtbot, operations, page)
    run = list(operations.records.values())[-1].identifier
    bundle = RunBundle(tmp_path / "LOG" / run)
    assert bundle.verify_integrity()["valid"]
    if damage == "foreign_metadata":
        data = json.loads(bundle.bundle_path.read_text(encoding="utf-8"))
        data["run_id"] = "RUN_FOREIGN"
        bundle.bundle_path.write_text(json.dumps(data), encoding="utf-8")
    elif damage == "missing_manifest":
        bundle.checksums_path.unlink()
    else:
        path = bundle.root / "run_state.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["out_dir"] = str(tmp_path / "FOREIGN_OUT")
        path.write_text(json.dumps(data), encoding="utf-8")
    before = {str(p.relative_to(bundle.root)): p.read_bytes() for p in bundle.root.rglob("*") if p.is_file()}
    history = HistoryPage(page.context, page, page)
    qtbot.addWidget(history)
    history.resize(1200, 900)
    history.show()
    history.refresh()
    qtbot.waitUntil(lambda: history.model.rowCount() > 0 and not operations.active, timeout=30000)
    row = next(i for i in range(history.model.rowCount()) if history.model.run_at(i).run_id == run)
    index = history.model.index(row, 0)
    history.tracks.scrollTo(index)
    qtbot.mouseClick(history.tracks.viewport(), Qt.LeftButton, pos=history.tracks.visualRect(index).center())
    qtbot.waitUntil(lambda: history.adapter is not None and not operations.active, timeout=30000)
    assert history.buttons["publish_staged"].isEnabled()
    history.buttons["publish_staged"].click()
    qtbot.waitUntil(lambda: not operations.active, timeout=30000)
    record = operations.records["publish.staged:" + run]
    assert record.terminal == "failed", "Corrupt bundle was published and resealed"
    assert "PUBLISH_BUNDLE_INTEGRITY" in record.error.detail
    assert not list((tmp_path / "OUT").rglob("*.md"))
    assert not (tmp_path / "FOREIGN_OUT").exists()
    assert before == {str(p.relative_to(bundle.root)): p.read_bytes() for p in bundle.root.rglob("*") if p.is_file()}
    assert sum(call["path"] == "/responses" for call in transport.calls) == 2
    for item in operations.records.values():
        if item.dialog:
            item.dialog.close()


@pytest.mark.parametrize("damage", ["", "foreign_metadata", "missing_manifest"])
def test_publication_new_process_after_bytes_before_bundle_update(qtbot, monkeypatch, tmp_path, damage):
    from kajovo.core.orchestration.publish import publish_staged_run
    from test_runtime_end_to_end import child

    transport = WorkflowHttp(tmp_path)
    page, operations = page_fixture(qtbot, monkeypatch, tmp_path, "QFILE", transport)
    for _ in range(2):
        page.start_button.click()
        settle(qtbot, operations, page)
    run = list(operations.records.values())[-1].identifier
    bundle = RunBundle(tmp_path / "LOG" / run)
    (tmp_path / "publish-run.txt").write_text(str(bundle.root), encoding="utf-8")
    with monkeypatch.context() as fault:
        def fail_record(*args, **kwargs):
            raise OSError("syntetické selhání evidence po zápisu bytes")
        fault.setattr(RunBundle, "update_run", fail_record)
        with pytest.raises(OSError, match="po zápisu bytes"):
            publish_staged_run(bundle.root)
    target = tmp_path / "OUT/výsledek/navrh.md"
    assert target.read_bytes() == "# Výsledek\nPřesné bytes.\n".encode()
    if damage == "foreign_metadata":
        metadata = json.loads(bundle.bundle_path.read_text(encoding="utf-8"))
        metadata["bundle_id"] = "bundle_foreign"
        bundle.bundle_path.write_text(json.dumps(metadata), encoding="utf-8")
    elif damage == "missing_manifest":
        bundle.checksums_path.unlink()
    before = {str(p.relative_to(bundle.root)): p.read_bytes() for p in bundle.root.rglob("*") if p.is_file()}
    requests = (tmp_path / "workflow-http.jsonl").read_bytes()
    child("from pathlib import Path; import sys; from test_history_publish_integrity_closure import publication_restart; "
          f"publication_restart(Path(sys.argv[1]), {bool(damage)!r})", tmp_path)
    assert (tmp_path / "workflow-http.jsonl").read_bytes() == requests
    assert target.read_bytes() == "# Výsledek\nPřesné bytes.\n".encode()
    if damage:
        assert before == {str(p.relative_to(bundle.root)): p.read_bytes() for p in bundle.root.rglob("*") if p.is_file()}
    else:
        assert bundle.verify_integrity()["valid"]


def publication_restart(root, blocked):
    from pathlib import Path
    from kajovo.core.orchestration.errors import OrchestrationError
    from kajovo.core.orchestration.publish import publish_staged_run, recover_publish_journal

    run = Path((root / "publish-run.txt").read_text(encoding="utf-8"))
    if blocked:
        with pytest.raises(OrchestrationError, match="PUBLISH_BUNDLE_INTEGRITY"):
            recover_publish_journal(run)
        with pytest.raises(OrchestrationError, match="PUBLISH_BUNDLE_INTEGRITY"):
            publish_staged_run(run)
    else:
        assert publish_staged_run(run)["status"] == "committed"
        bundle = RunBundle(run)
        assert bundle.verify_integrity()["valid"]
        assert bundle.run_record()["status"] == "completed_unverified"
        before = {str(p.relative_to(run)): p.read_bytes() for p in run.rglob("*") if p.is_file()}
        times = {str(p.relative_to(run)): p.stat().st_mtime_ns for p in run.rglob("*") if p.is_file()}
        assert publish_staged_run(run)["status"] == "committed"
        assert before == {str(p.relative_to(run)): p.read_bytes() for p in run.rglob("*") if p.is_file()}
        assert times == {str(p.relative_to(run)): p.stat().st_mtime_ns for p in run.rglob("*") if p.is_file()}
