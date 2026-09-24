"""Sdílené zámky dávky, publikace a bezpečná obnova přerušeného zápisu."""
import hashlib
import json
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from kajovo.core.batch_completion import complete_saved_batch, recover_unknown_submission, remember_remote_batch_state
from kajovo.core.generate_batch import process_saved_batch
from kajovo.core.orchestration import publish
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.runs.locking import ExecutionLock


def test_batch_writers_and_publication_respect_execution_lock(tmp_path):
    root = tmp_path / "RUN_LOCK"
    root.mkdir()
    state = root / "run_state.json"
    state.write_text(json.dumps({"batch_id": "batch", "staged_files": [{"path": "new.txt"}]}), encoding="utf-8")
    before = state.read_bytes()
    client = Mock()
    with ExecutionLock(root / "execution.lock"):
        assert remember_remote_batch_state(root, {"id": "batch", "status": "completed"}) is False
        for operation in (
            lambda: recover_unknown_submission(root, []),
            lambda: complete_saved_batch(client, root, "batch", None),
            lambda: process_saved_batch(client, root, "batch", None),
            lambda: publish.publish_staged_run(root),
            lambda: publish.recover_publish_journal(root),
        ):
            with pytest.raises(BlockingIOError):
                operation()
    assert state.read_bytes() == before
    assert not client.mock_calls
    assert remember_remote_batch_state(root, {"id": "batch", "status": "completed"})
    assert json.loads(state.read_text("utf-8"))["staged_files"] == [{"path": "new.txt"}]


def test_rollback_checks_link_boundary_before_unlink(tmp_path, monkeypatch):
    target = tmp_path / "new.txt"
    target.write_bytes(b"new")
    row = {"path": "new.txt", "new_hash": hashlib.sha256(b"new").hexdigest(),
           "expected_old_hash": None, "backup_ref": None}

    def reject_link(root, relative):
        raise OrchestrationError("PUBLISH_LINK_BOUNDARY", relative)

    monkeypatch.setattr(publish, "_assert_no_link_boundary", reject_link)
    with pytest.raises(OrchestrationError, match="PUBLISH_LINK_BOUNDARY"):
        publish._restore_old(tmp_path, tmp_path, row)
    assert target.read_bytes() == b"new"


@pytest.mark.parametrize("directory_link", [False, True])
def test_recovery_never_deletes_symlink_target(tmp_path, directory_link):
    root, out = tmp_path / "RUN_LINK", tmp_path / "out"
    staging = root / "staging"
    staging.mkdir(parents=True)
    out.mkdir()
    rows = []
    for name in ("nested/a.txt", "b.txt"):
        source = staging / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"new")
        rows.append({"path": name, "staged_path": source.relative_to(root).as_posix(),
                     "sha256": hashlib.sha256(b"new").hexdigest()})
    plan = publish.prepare_publish(rows, out, {row["path"]: None for row in rows}, run_dir=root)
    foreign = out / "foreign"
    foreign.mkdir()
    target = foreign / "a.txt"
    target.write_bytes(b"new")
    try:
        if directory_link:
            (out / "nested").symlink_to(foreign, target_is_directory=True)
        else:
            (out / "nested").mkdir()
            (out / "nested/a.txt").symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Prostředí nepovoluje symlinky: {exc}")
    journal = {"version": 2, "plan": plan.to_dict(), "state": "applying",
               "entries": [{**asdict(row), "state": "write_intent"} for row in plan.entries]}
    publish._write_journal(root / "manifests/publish_journal.json", journal)
    with pytest.raises(OrchestrationError, match="PUBLISH_LINK_BOUNDARY"):
        publish.recover_publish_journal(root)
    assert target.read_bytes() == b"new"
    assert not (out / "b.txt").exists()
