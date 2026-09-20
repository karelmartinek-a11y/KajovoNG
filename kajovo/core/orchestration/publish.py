"""Recoverable conflict-safe publication from immutable staging into OUT."""
from __future__ import annotations

import contextlib
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
    is_junction = getattr(os.path, "isjunction", lambda value: False)
    for part in relative.split("/")[:-1]:
        current = current / part
        if current.exists() and (current.is_symlink() or is_junction(str(current))):
            raise OrchestrationError(
                "PUBLISH_LINK_BOUNDARY",
                f"Publikace odmítá odkaz v cílové cestě: {relative}",
            )


def _fsync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".kajovo_publish_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def _write_journal(path: Path, journal: dict[str, Any]) -> None:
    _write_bytes_atomic(
        path,
        (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _hash_if_file(path: Path) -> str | None:
    return sha256_file(str(path)) if path.is_file() else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


class _TargetPublishLock:
    def __init__(self, target_root: Path):
        self.target_root = target_root
        self.path = target_root / ".kajovo_publish.lock"
        self.pid = os.getpid()
        self.acquired = False

    def acquire(self) -> None:
        self.target_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": self.pid, "created_at": time.time()}).encode("utf-8")
        for _attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    row = json.loads(self.path.read_text(encoding="utf-8"))
                    owner = int(row.get("pid") or 0)
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    owner = 0
                if owner and _pid_alive(owner):
                    raise OrchestrationError(
                        "PUBLISH_LOCKED",
                        f"Cílový kořen právě publikuje jiný proces (pid={owner}).",
                    )
                with contextlib.suppress(FileNotFoundError):
                    self.path.unlink()
                _fsync_directory(self.target_root)
                continue
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_directory(self.target_root)
            self.acquired = True
            return
        raise OrchestrationError("PUBLISH_LOCKED", str(self.target_root))

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            row = json.loads(self.path.read_text(encoding="utf-8"))
            if int(row.get("pid") or 0) == self.pid:
                self.path.unlink()
                _fsync_directory(self.target_root)
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def prepare_publish(
    staging: list[dict[str, Any]],
    target: str | Path,
    expected: dict[str, str | None],
    *,
    run_dir: str | Path,
) -> PublishPlan:
    target_root = Path(target).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(run_dir).resolve()
    undo_root = run_root / "staging" / "publish_undo"
    staging_root = run_root / "staging" / "candidate" / "generated"
    ensure_dir(str(undo_root))

    entries: list[PublishEntry] = []
    required_bytes = 0
    seen: set[str] = set()
    for row in staging:
        relative = str(row.get("path") or "")
        staged_rel = str(row.get("staged_path") or "")
        if not relative or not staged_rel:
            raise OrchestrationError("PUBLISH_STAGING_INVALID", "Staged item nemá cestu.")
        normalized_key = os.path.normcase(relative.replace("\\", "/"))
        if normalized_key in seen:
            raise OrchestrationError(
                "PUBLISH_PATH_COLLISION", f"Duplicitní/case-collision cesta: {relative}"
            )
        seen.add(normalized_key)
        if relative not in expected:
            raise OrchestrationError(
                "PUBLISH_EXPECTATION_MISSING",
                f"{relative}: chybí původní očekávaný stav cíle.",
            )

        staged_path = (run_root / staged_rel).resolve()
        try:
            staged_path.relative_to(run_root)
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
        current_hash = _hash_if_file(destination)
        expected_hash = expected[relative]
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
            with backup_path.open("rb") as handle:
                os.fsync(handle.fileno())
            _fsync_directory(backup_path.parent)
            backup_hash = sha256_file(str(backup_path))
            if backup_hash != current_hash:
                raise OrchestrationError("PUBLISH_BACKUP_HASH", relative)
            backup_ref = backup_path.relative_to(run_root).as_posix()

        required_bytes += staged_path.stat().st_size
        entries.append(
            PublishEntry(
                path=relative,
                staged_path=staged_path.relative_to(run_root).as_posix(),
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


def _journal_report(journal: dict[str, Any], journal_path: Path, run_root: Path) -> dict[str, Any]:
    published = []
    for row in journal.get("entries") or []:
        if row.get("state") == "written_verified":
            destination = Path(
                safe_join_under_root(str(Path(journal["plan"]["target_root"]).resolve()), row["path"])
            )
            published.append({
                "path": row["path"],
                "sha256": row["new_hash"],
                "bytes": destination.stat().st_size if destination.is_file() else 0,
            })
    return {
        "plan_id": journal["plan"]["plan_id"],
        "status": journal["state"],
        "published": published,
        "journal": journal_path.relative_to(run_root).as_posix(),
    }


def _restore_old(run_root: Path, target_root: Path, row: dict[str, Any]) -> None:
    destination = Path(safe_join_under_root(str(target_root), row["path"]))
    actual = _hash_if_file(destination)
    if actual != row["new_hash"]:
        if actual == row["expected_old_hash"]:
            return
        raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", row["path"])
    backup_ref = row.get("backup_ref")
    if backup_ref:
        backup = (run_root / str(backup_ref)).resolve()
        try:
            backup.relative_to(run_root)
        except ValueError as exc:
            raise OrchestrationError("PUBLISH_BACKUP_ESCAPE", row["path"]) from exc
        if not backup.is_file():
            raise OrchestrationError("PUBLISH_BACKUP_MISSING", row["path"])
        _write_bytes_atomic(destination, backup.read_bytes())
        if _hash_if_file(destination) != row["expected_old_hash"]:
            raise OrchestrationError("PUBLISH_ROLLBACK_HASH", row["path"])
    elif destination.exists():
        destination.unlink()
        _fsync_directory(destination.parent)


def _recover_journal_locked(
    journal_path: Path,
    run_root: Path,
    journal: dict[str, Any],
) -> dict[str, Any]:
    if journal.get("version") != 2 or not isinstance(journal.get("plan"), dict):
        raise OrchestrationError("PUBLISH_JOURNAL_VERSION", str(journal_path))
    target_root = Path(journal["plan"]["target_root"]).resolve()
    state = str(journal.get("state") or "")
    if state in {"committed", "rolled_back"}:
        return _journal_report(journal, journal_path, run_root)
    if state == "conflict":
        raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", str(target_root))

    entries = journal.get("entries")
    if not isinstance(entries, list):
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", str(journal_path))

    foreign: list[str] = []
    new_count = 0
    for row in entries:
        destination = Path(safe_join_under_root(str(target_root), row["path"]))
        actual = _hash_if_file(destination)
        if actual == row["new_hash"]:
            row["state"] = "written_verified"
            new_count += 1
        elif actual == row["expected_old_hash"]:
            row["state"] = "prepared"
        else:
            row["state"] = "conflict"
            foreign.append(row["path"])

    if foreign:
        journal["state"] = "conflict"
        journal["conflicts"] = foreign
        _write_journal(journal_path, journal)
        raise OrchestrationError(
            "PUBLISH_RECOVERY_CONFLICT",
            "Cizí změna během obnovy: " + ", ".join(foreign),
        )

    if entries and new_count == len(entries):
        journal["state"] = "committed"
        journal["recovered_commit"] = True
        journal["finished_at"] = time.time()
        _write_journal(journal_path, journal)
        return _journal_report(journal, journal_path, run_root)

    # Smíšený stav není commit celé sady. Vrať pouze hodnoty, které stále
    # přesně odpovídají našemu new_hash; cizí obsah se nikdy nepřepisuje.
    rollback_conflicts: list[str] = []
    for row in reversed(entries):
        destination = Path(safe_join_under_root(str(target_root), row["path"]))
        actual = _hash_if_file(destination)
        if actual != row["new_hash"]:
            continue
        try:
            _restore_old(run_root, target_root, row)
            row["state"] = "rolled_back"
        except OrchestrationError:
            rollback_conflicts.append(row["path"])
    journal["state"] = "conflict" if rollback_conflicts else "rolled_back"
    journal["rollback_conflicts"] = rollback_conflicts
    journal["recovered_at"] = time.time()
    _write_journal(journal_path, journal)
    if rollback_conflicts:
        raise OrchestrationError(
            "PUBLISH_RECOVERY_CONFLICT",
            "Rollback konflikt: " + ", ".join(rollback_conflicts),
        )
    return _journal_report(journal, journal_path, run_root)


def recover_publish_journal(run_dir: str | Path) -> dict[str, Any] | None:
    """Idempotentně dokončí commit nebo rollback po tvrdém pádu procesu."""
    run_root = Path(run_dir).resolve()
    journal_path = run_root / "manifests" / "publish_journal.json"
    if not journal_path.is_file():
        return None
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        target_root = Path(journal["plan"]["target_root"]).resolve()
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", str(journal_path)) from exc
    with _TargetPublishLock(target_root):
        return _recover_journal_locked(journal_path, run_root, journal)


def commit_publish(plan: PublishPlan, *, run_dir: str | Path) -> dict[str, Any]:
    run_root = Path(run_dir).resolve()
    target_root = Path(plan.target_root).resolve()
    journal_path = run_root / "manifests" / "publish_journal.json"
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    with _TargetPublishLock(target_root):
        if journal_path.is_file():
            existing = json.loads(journal_path.read_text(encoding="utf-8"))
            report = _recover_journal_locked(journal_path, run_root, existing)
            if report["status"] == "committed":
                if existing["plan"]["plan_id"] != plan.plan_id:
                    raise OrchestrationError(
                        "PUBLISH_ALREADY_COMMITTED",
                        "Run už obsahuje jinou commitnutou publikační transakci.",
                    )
                return report

        journal = {
            "version": 2,
            "plan": plan.to_dict(),
            "state": "prepared",
            "entries": [
                {**asdict(entry), "state": "prepared"}
                for entry in plan.entries
            ],
            "started_at": time.time(),
        }
        _write_journal(journal_path, journal)

        try:
            for row in journal["entries"]:
                destination = Path(safe_join_under_root(str(target_root), row["path"]))
                _assert_no_link_boundary(target_root, row["path"])
                current_hash = _hash_if_file(destination)
                if current_hash != row["expected_old_hash"]:
                    raise OrchestrationError("PUBLISH_CONFLICT", row["path"])

                # Durable intent exists before OUT can change.
                row["state"] = "write_intent"
                row["intent_at"] = time.time()
                journal["state"] = "applying"
                _write_journal(journal_path, journal)

                staged = (run_root / row["staged_path"]).resolve()
                try:
                    staged.relative_to(run_root)
                except ValueError as exc:
                    raise OrchestrationError("PUBLISH_STAGING_ESCAPE", row["path"]) from exc
                data = staged.read_bytes()
                if hashlib.sha256(data).hexdigest() != row["new_hash"]:
                    raise OrchestrationError("PUBLISH_STAGING_HASH", row["path"])
                _write_bytes_atomic(destination, data)
                actual = _hash_if_file(destination)
                if actual != row["new_hash"]:
                    raise OrchestrationError("PUBLISH_WRITE_HASH", row["path"])

                row["state"] = "written_verified"
                row["verified_at"] = time.time()
                _write_journal(journal_path, journal)
        except Exception:
            # Recovery includes the write_intent item even if the process failed
            # after mutation but before the success marker.
            try:
                _recover_journal_locked(journal_path, run_root, journal)
            except OrchestrationError:
                pass
            raise

        journal["state"] = "committed"
        journal["finished_at"] = time.time()
        _write_journal(journal_path, journal)
        return _journal_report(journal, journal_path, run_root)


def _apply_committed_report_to_state(
    state_path: Path,
    state: dict[str, Any],
    report: dict[str, Any],
) -> None:
    state["published_files"] = report["published"]
    state["publish_report"] = report
    state["unverified_publish_approved"] = True
    state["status"] = "completed_unverified"
    state["publication_state"] = "published_unverified"
    _write_bytes_atomic(
        state_path,
        (json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8"),
    )


def publish_staged_run(run_dir: str | Path) -> dict[str, Any]:
    """Explicit user action; recovers any prior transaction before new publication."""
    run_root = Path(run_dir).resolve()
    state_path = run_root / "run_state.json"
    if not state_path.is_file():
        raise OrchestrationError("PUBLISH_RUN_MISSING", str(run_root))

    recovery = recover_publish_journal(run_root)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if recovery and recovery.get("status") == "committed":
        _apply_committed_report_to_state(state_path, state, recovery)
        return recovery

    if state.get("dry_run"):
        raise OrchestrationError("PUBLISH_DRY_RUN", "Dry-run nikdy nepublikuje OUT.")
    staged = state.get("staged_files")
    if not isinstance(staged, list) or not staged:
        raise OrchestrationError("PUBLISH_NOTHING_STAGED", "Běh nemá staged artefakty.")
    if state.get("published_files"):
        return {"status": "already_published", "published": state["published_files"]}

    out_dir = str(state.get("out_dir") or "")
    if not out_dir:
        raise OrchestrationError("PUBLISH_TARGET_MISSING", "Běh nemá OUT.")
    expected: dict[str, str | None] = {}
    for row in staged:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        if "expected_target_hash" not in row:
            raise OrchestrationError(
                "PUBLISH_EXPECTATION_MISSING",
                f"{row.get('path')}: staged manifest nemá původní očekávání.",
            )
        expected[str(row["path"])] = row["expected_target_hash"]

    plan = prepare_publish(staged, out_dir, expected, run_dir=run_root)
    report = commit_publish(plan, run_dir=run_root)
    _apply_committed_report_to_state(state_path, state, report)
    try:
        from ..run_bundle import RunBundle
        bundle = RunBundle(run_root)
        bundle.update_run({"status": "completed_unverified", "result_class": "completed_unverified"})
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
        pass
    return report
