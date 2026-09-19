"""Provozní zámek nesmí znehodnotit trvalé podklady obnovy."""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QLockFile

from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.runlog import RunLogger
from kajovo.studio.history_launcher import HistoryBranchLauncher
from test_preparation_snapshot import snapshot


def test_seal_during_execution_survives_unlock(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_LOCK", "Projekt")
    root = Path(logger.paths.run_dir)
    lock = QLockFile(str(root / "execution.lock"))
    assert lock.tryLock(0)
    try:
        logger.bundle.seal()
        manifest = json.loads(logger.bundle.checksums_path.read_text("utf-8"))
        assert "execution.lock" not in manifest["files"]
    finally:
        lock.unlock()
    assert logger.bundle.verify_integrity()["valid"]


@pytest.mark.parametrize("lock_state", ["missing", "changed", "original"])
def test_legacy_lock_keeps_manifest_and_all_artifact_checks(tmp_path, monkeypatch, lock_state):
    logger = RunLogger(str(tmp_path), "RUN_LEGACY_LOCK", "Projekt")
    root = Path(logger.paths.run_dir)
    saved = snapshot(stage="A1")
    saved["structure"] = None
    from kajovo.core.generate_batch import digest
    saved.pop("snapshot_hash")
    saved["snapshot_hash"] = digest(saved)
    logger.update_state({"ui_state": {
        "mode": "GENERATE", "prompt": "test", "maximum_quality": False,
        "stop_after_plan": False, "dry_run": False,
    }})
    logger.update_state({"preparation_snapshot": saved, "status": "failed"})
    lock = root / "execution.lock"
    lock.write_text("original process", encoding="utf-8")
    artifact = root / "artifacts" / "execution.lock"
    artifact.write_text("archived evidence", encoding="utf-8")
    files = logger.bundle._checksum_files
    with monkeypatch.context() as patch:
        patch.setattr(logger.bundle, "_checksum_files", lambda: files() + [lock])
        logger.bundle.seal()
    before = logger.bundle.checksums_path.read_bytes()
    if lock_state == "missing":
        lock.unlink()
    elif lock_state == "changed":
        lock.write_text("another process", encoding="utf-8")
    assert logger.bundle.verify_integrity()["valid"]
    adapter = LegacyRunAdapter(root)
    checkpoint = next(row for row in adapter.checkpoints() if row["checkpoint_type"] == "A1")
    preview = HistoryBranchLauncher(None).preview(adapter, checkpoint["checkpoint_id"], "rerun")
    assert preview.inherited_stages == ("A0R", "A1")
    assert preview.first_paid_operation == "A2"
    assert logger.bundle.checksums_path.read_bytes() == before
    artifact.write_text("changed evidence", encoding="utf-8")
    assert not logger.bundle.verify_integrity()["valid"]
    with pytest.raises(ValueError, match="integrity"):
        HistoryBranchLauncher(None).preview(adapter, checkpoint["checkpoint_id"], "rerun")
    artifact.unlink()
    assert "Chybí artifacts/execution.lock." in logger.bundle.verify_integrity()["errors"]
    assert logger.bundle.checksums_path.read_bytes() == before
