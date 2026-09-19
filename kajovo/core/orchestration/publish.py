"""Conflict-safe publication from immutable staging into OUT."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..utils import ensure_dir, safe_join_under_root, sha256_file
from .contracts import canonical_sha256
from .errors import OrchestrationError


@dataclass(frozen=True)
class PublishEntry:
    path: str
    staged_path: str
    expected_old_hash: str | None
    new_hash: str
    backup_ref: str | None


@dataclass(frozen=True)
class PublishPlan:
    plan_id: str
    target_root: str
    staging_root: str
    undo_root: str
    entries: tuple[PublishEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "target_root": self.target_root,
            "staging_root": self.staging_root,
            "undo_root": self.undo_root,
            "entries": [asdict(item) for item in self.entries],
        }


def _assert_no_link_boundary(root: Path, relative: str) -> None:
    current = root
    for part in relative.split("/")[:-1]:
        current = current / part
        if current.exists() and (current.is_symlink() or os.path.isjunction(current)):
            raise OrchestrationError(
                "PUBLISH_LINK_BOUNDARY",
                f"Publikace odmítá odkaz v cílové cestě: {relative}",
            )


def prepare_publish(
    staging: list[dict[str, Any]],
    target: str | Path,
    expected: dict[str, str | None],
    *,
    run_dir: str | Path,
) -> PublishPlan:
    target_root = Path(target).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    undo_root = Path(run_dir).resolve() / "staging" / "publish_undo"
    staging_root = Path(run_dir).resolve() / "staging" / "candidate" / "generated"
    ensure_dir(str(undo_root))

    entries: list[PublishEntry] = []
    required_bytes = 0
    for row in staging:
        relative = str(row.get("path") or "")
        staged_rel = str(row.get("staged_path") or "")
        if not relative or not staged_rel:
            raise OrchestrationError("PUBLISH_STAGING_INVALID", "Staged item nemá cestu.")
        staged_path = (Path(run_dir).resolve() / staged_rel).resolve()
        try:
            staged_path.relative_to(Path(run_dir).resolve())
        except ValueError as exc:
            raise OrchestrationError("PUBLISH_STAGING_ESCAPE", relative) from exc
        if not staged_path.is_file():
            raise OrchestrationError("PUBLISH_STAGING_MISSING", relative)
        staged_hash = sha256_file(str(staged_path))
        declared_hash = str(row.get("sha256") or "")
        if staged_hash != declared_hash:
            raise OrchestrationError("PUBLISH_STAGING_HASH", relative)

        destination = Path(safe_join_under_root(str(target_root), relative))
        _assert_no_link_boundary(target_root, relative)
        current_hash = sha256_file(str(destination)) if destination.is_file() else None
        expected_hash = expected.get(relative)
        if current_hash != expected_hash:
            raise OrchestrationError(
                "PUBLISH_CONFLICT",
                f"{relative}: expected {expected_hash!r}, actual {current_hash!r}",
            )
        backup_ref = None
        if destination.is_file():
            backup_path = undo_root / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup_path)
            backup_hash = sha256_file(str(backup_path))
            if backup_hash != current_hash:
                raise OrchestrationError("PUBLISH_BACKUP_HASH", relative)
            backup_ref = backup_path.relative_to(Path(run_dir).resolve()).as_posix()
        required_bytes += staged_path.stat().st_size
        entries.append(
            PublishEntry(
                path=relative,
                staged_path=staged_path.relative_to(Path(run_dir).resolve()).as_posix(),
                expected_old_hash=expected_hash,
                new_hash=staged_hash,
                backup_ref=backup_ref,
            )
        )

    if shutil.disk_usage(target_root).free < required_bytes:
        raise OrchestrationError("PUBLISH_NO_SPACE", "OUT nemá dostatek volného místa.")
    seed = {
        "target_root": str(target_root),
        "entries": [asdict(row) for row in entries],
    }
    return PublishPlan(
        "PUBLISH-" + canonical_sha256(seed)[:32],
        str(target_root),
        str(staging_root),
        str(undo_root),
        tuple(entries),
    )


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".kajovo_publish_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def commit_publish(plan: PublishPlan, *, run_dir: str | Path) -> dict[str, Any]:
    run_root = Path(run_dir).resolve()
    target_root = Path(plan.target_root).resolve()
    journal_path = run_root / "manifests" / "publish_journal.json"
    journal = {
        "version": 1,
        "plan": plan.to_dict(),
        "state": "prepared",
        "applied": [],
        "started_at": time.time(),
    }
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes_atomic(
        journal_path,
        (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )

    applied: list[dict[str, Any]] = []
    try:
        # Re-check every target before first mutation.
        for entry in plan.entries:
            destination = Path(
                safe_join_under_root(str(target_root), entry.path)
            )
            _assert_no_link_boundary(target_root, entry.path)
            current_hash = sha256_file(str(destination)) if destination.is_file() else None
            if current_hash != entry.expected_old_hash:
                raise OrchestrationError("PUBLISH_CONFLICT", entry.path)

        for entry in plan.entries:
            destination = Path(
                safe_join_under_root(str(target_root), entry.path)
            )
            staged = (run_root / entry.staged_path).resolve()
            data = staged.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry.new_hash:
                raise OrchestrationError("PUBLISH_STAGING_HASH", entry.path)
            _write_bytes_atomic(destination, data)
            actual = sha256_file(str(destination))
            if actual != entry.new_hash:
                raise OrchestrationError("PUBLISH_WRITE_HASH", entry.path)
            applied.append({
                "path": entry.path,
                "sha256": actual,
                "bytes": destination.stat().st_size,
            })
            journal["state"] = "applying"
            journal["applied"] = applied
            _write_bytes_atomic(
                journal_path,
                (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
    except Exception:
        # Roll back only if the current target still equals the value written by
        # this publish operation. Never overwrite a newer user edit.
        conflicts: list[str] = []
        for done in reversed(applied):
            entry = next(item for item in plan.entries if item.path == done["path"])
            destination = Path(
                safe_join_under_root(str(target_root), entry.path)
            )
            current_hash = sha256_file(str(destination)) if destination.is_file() else None
            if current_hash != entry.new_hash:
                conflicts.append(entry.path)
                continue
            if entry.backup_ref:
                backup = run_root / entry.backup_ref
                _write_bytes_atomic(destination, backup.read_bytes())
            elif destination.exists():
                destination.unlink()
        journal["state"] = "conflict" if conflicts else "rolled_back"
        journal["rollback_conflicts"] = conflicts
        _write_bytes_atomic(
            journal_path,
            (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        raise

    journal["state"] = "committed"
    journal["finished_at"] = time.time()
    _write_bytes_atomic(
        journal_path,
        (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return {
        "plan_id": plan.plan_id,
        "status": "committed",
        "published": applied,
        "journal": journal_path.relative_to(run_root).as_posix(),
    }


def publish_staged_run(run_dir: str | Path) -> dict[str, Any]:
    """Explicit user action for unverified staged artifacts."""
    run_root = Path(run_dir).resolve()
    state_path = run_root / "run_state.json"
    if not state_path.is_file():
        raise OrchestrationError("PUBLISH_RUN_MISSING", str(run_root))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("dry_run"):
        raise OrchestrationError("PUBLISH_DRY_RUN", "Dry-run nikdy nepublikuje OUT.")
    staged = state.get("staged_files")
    if not isinstance(staged, list) or not staged:
        raise OrchestrationError("PUBLISH_NOTHING_STAGED", "Běh nemá staged artefakty.")
    if state.get("published_files"):
        return {
            "status": "already_published",
            "published": state["published_files"],
        }
    out_dir = str(state.get("out_dir") or "")
    if not out_dir:
        raise OrchestrationError("PUBLISH_TARGET_MISSING", "Běh nemá OUT.")
    expected = {
        str(row["path"]): row.get("expected_target_hash")
        for row in staged
        if isinstance(row, dict) and row.get("path")
    }
    plan = prepare_publish(staged, out_dir, expected, run_dir=run_root)
    report = commit_publish(plan, run_dir=run_root)
    state["published_files"] = report["published"]
    state["publish_report"] = report
    state["unverified_publish_approved"] = True
    state["status"] = "completed_unverified"
    state["publication_state"] = "published_unverified"
    _write_bytes_atomic(
        state_path,
        (json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8"),
    )
    try:
        from ..run_bundle import RunBundle
        bundle = RunBundle(run_root)
        bundle.update_run({
            "status": "completed_unverified",
            "result_class": "completed_unverified",
        })
        bundle.append_event(
            "publication.completed_unverified",
            {
                "plan_id": report["plan_id"],
                "published": report["published"],
                "explicit_user_take": True,
            },
            source_module="orchestration.publish",
            operation="PUBLISH",
            human_message="Uživatel výslovně převzal staged artefakty bez úplného funkčního ověření.",
        )
        bundle.seal()
    except (OSError, ValueError, KeyError):
        # Publikace je již doložena durable journalem; chyba sekundární indexace
        # nesmí předstírat rollback úspěšně commitnutého OUT.
        pass
    return report
