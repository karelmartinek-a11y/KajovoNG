"""Recoverable conflict-safe publication from immutable staging into OUT."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from ..runs.locking import ExecutionLock

from ..utils import ensure_dir, safe_join_under_root, sha256_file, validate_relative_path
from .contracts import canonical_bytes, canonical_sha256, parse_json_strict
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
    validate_relative_path(relative)
    for part in relative.replace("\\", "/").split("/"):
        current = current / part
        if current.is_symlink() or is_junction(str(current)):
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
    _write_bytes_atomic(path, canonical_bytes(journal) + b"\n")


def _read_journal(path: Path) -> dict[str, Any]:
    try:
        return parse_json_strict(path.read_text(encoding="utf-8"))
    except (OSError, OrchestrationError) as exc:
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", str(path)) from exc


def _hash_if_file(path: Path) -> str | None:
    return sha256_file(str(path)) if path.is_file() else None


def _validate_journal_plan(journal: dict[str, Any], run_root: Path) -> None:
    plan = journal.get("plan")
    entries = journal.get("entries")
    if not isinstance(plan, dict) or not isinstance(entries, list) or not entries:
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", str(run_root))
    planned = plan.get("entries")
    if not isinstance(planned, list) or len(planned) != len(entries):
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", "Neúplná sada položek.")
    fields = {"path", "staged_path", "expected_old_hash", "new_hash", "backup_ref"}
    normalized = []
    for row, original in zip(entries, planned, strict=True):
        if not isinstance(row, dict) or not isinstance(original, dict) or set(original) != fields:
            raise OrchestrationError("PUBLISH_JOURNAL_INVALID", "Neplatná položka.")
        if {key: row.get(key) for key in fields} != original:
            raise OrchestrationError("PUBLISH_JOURNAL_INVALID", "Položka změnila zmrazený plán.")
        for field in ("path", "staged_path"):
            validate_relative_path(original[field])
        for field in ("staged_path", "backup_ref"):
            if original[field] is not None:
                path = (run_root / original[field]).resolve()
                try:
                    path.relative_to(run_root)
                except ValueError as exc:
                    raise OrchestrationError("PUBLISH_JOURNAL_ESCAPE", str(original[field])) from exc
        normalized.append(os.path.normcase(original["path"].replace("\\", "/")))
    if len(normalized) != len(set(normalized)):
        raise OrchestrationError("PUBLISH_PATH_COLLISION", "Journal obsahuje duplicitní cestu.")
    seed = {"target_root": plan["target_root"], "entries": planned}
    if plan.get("plan_id") != "PUBLISH-" + canonical_sha256(seed)[:32]:
        raise OrchestrationError("PUBLISH_JOURNAL_HASH", str(run_root))


class TargetPublishLock:
    """Procesový zámek OS; pád uvolní handle bez mazání souboru jiného procesu."""
    def __init__(self, target_root: Path):
        self.target_root = target_root.resolve()
        key = hashlib.sha256(os.path.normcase(str(self.target_root)).encode("utf-8")).hexdigest()
        directory = Path(tempfile.gettempdir()) / "kajovo-publish-locks"
        if directory.is_symlink():
            raise OrchestrationError("PUBLISH_LOCK_UNSAFE", str(directory))
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / (key + ".lock")
        self.handle = None

    def acquire(self) -> None:
        if self.path.is_symlink() or getattr(os.path, "isjunction", lambda p: False)(str(self.path)):
            raise OrchestrationError("PUBLISH_LOCK_UNSAFE", str(self.path))
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self.path), flags, 0o600)
        handle = os.fdopen(fd, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise OrchestrationError("PUBLISH_LOCKED", str(self.target_root)) from exc
        self.handle = handle

    def release(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

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
            # Záloha je obsahově adresovaná a nelze ji přepsat jiným plánem.
            original = destination.read_bytes()
            if hashlib.sha256(original).hexdigest() != current_hash:
                raise OrchestrationError("PUBLISH_CONFLICT", relative)
            backup_path = undo_root / (str(current_hash) + ".bin")
            if not backup_path.exists():
                _write_bytes_atomic(backup_path, original)
            if not backup_path.is_file() or sha256_file(str(backup_path)) != current_hash:
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
    _assert_no_link_boundary(target_root, row["path"])
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
        original = backup.read_bytes()
        if hashlib.sha256(original).hexdigest() != row["expected_old_hash"]:
            raise OrchestrationError("PUBLISH_BACKUP_HASH", row["path"])
        _assert_no_link_boundary(target_root, row["path"])
        if _hash_if_file(destination) != row["new_hash"]:
            raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", row["path"])
        _write_bytes_atomic(destination, original)
        if _hash_if_file(destination) != row["expected_old_hash"]:
            raise OrchestrationError("PUBLISH_ROLLBACK_HASH", row["path"])
    elif destination.exists():
        _assert_no_link_boundary(target_root, row["path"])
        if _hash_if_file(destination) != row["new_hash"]:
            raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", row["path"])
        destination.unlink()
        _fsync_directory(destination.parent)


def _recover_journal_locked(
    journal_path: Path,
    run_root: Path,
    journal: dict[str, Any],
) -> dict[str, Any]:
    if journal.get("version") != 2 or not isinstance(journal.get("plan"), dict):
        raise OrchestrationError("PUBLISH_JOURNAL_VERSION", str(journal_path))
    _validate_journal_plan(journal, run_root)
    target_root = Path(journal["plan"]["target_root"]).resolve()
    state = str(journal.get("state") or "")
    if state in {"committed", "rolled_back"}:
        field = "new_hash" if state == "committed" else "expected_old_hash"
        for row in journal["entries"]:
            _assert_no_link_boundary(target_root, row["path"])
            destination = Path(safe_join_under_root(str(target_root), row["path"]))
            if _hash_if_file(destination) != row[field]:
                raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", row["path"])
        return _journal_report(journal, journal_path, run_root)
    if state == "conflict":
        raise OrchestrationError("PUBLISH_RECOVERY_CONFLICT", str(target_root))

    entries = journal.get("entries")
    if not isinstance(entries, list):
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", str(journal_path))

    foreign: list[str] = []
    new_count = 0
    for row in entries:
        _assert_no_link_boundary(target_root, row["path"])
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
    with ExecutionLock(Path(run_dir) / "execution.lock"):
        return recover_publish_journal_already_locked(run_dir)


def recover_publish_journal_already_locked(run_dir: str | Path) -> dict[str, Any] | None:
    """Obnova pro volajícího, který už drží execution.lock tohoto běhu."""
    run_root = Path(run_dir).resolve()
    journal_path = run_root / "manifests" / "publish_journal.json"
    if not journal_path.is_file():
        return None
    journal = _read_journal(journal_path)
    try:
        target_root = Path(journal["plan"]["target_root"]).resolve()
    except (KeyError, TypeError) as exc:
        raise OrchestrationError("PUBLISH_JOURNAL_INVALID", str(journal_path)) from exc
    with TargetPublishLock(target_root):
        return _recover_journal_locked(journal_path, run_root, journal)


def commit_publish(plan: PublishPlan, *, run_dir: str | Path) -> dict[str, Any]:
    with ExecutionLock(Path(run_dir) / "execution.lock"):
        return commit_publish_already_locked(plan, run_dir=run_dir)


def commit_publish_already_locked(plan: PublishPlan, *, run_dir: str | Path) -> dict[str, Any]:
    """Commit pro volajícího, který už drží execution.lock tohoto běhu."""
    run_root = Path(run_dir).resolve()
    target_root = Path(plan.target_root).resolve()
    journal_path = run_root / "manifests" / "publish_journal.json"
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    with TargetPublishLock(target_root):
        if journal_path.is_file():
            existing = _read_journal(journal_path)
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
        canonical_bytes(state) + b"\n",
    )


def _record_publication(run_root: Path, report: dict[str, Any]) -> None:
    """Dokončí evidenci i po pádu mezi publikací a zapečetěním běhu."""
    from ..run_bundle import LegacyRunAdapter, RunBundle
    if not (run_root / "bundle.json").is_file():
        return
    bundle = RunBundle(run_root)
    bundle.update_run({"status": "completed_unverified", "result_class": "completed_unverified"})
    recorded = any(
        event.get("event_type") == "publication.completed_unverified"
        and (event.get("data") or {}).get("plan_id") == report["plan_id"]
        for event in LegacyRunAdapter(run_root).events()
    )
    if not recorded:
        bundle.append_event(
            "publication.completed_unverified",
            {"plan_id": report["plan_id"], "published": report["published"], "explicit_user_take": True},
            source_module="orchestration.publish", operation="PUBLISH",
            human_message="Uživatel výslovně převzal staged artefakty.",
        )
    bundle.seal()


def publish_staged_run(run_dir: str | Path) -> dict[str, Any]:
    """Publikuje stabilní staging pod společným zámkem běhu."""
    with ExecutionLock(Path(run_dir) / "execution.lock"):
        return publish_staged_run_already_locked(run_dir)


def publish_staged_run_already_locked(run_dir: str | Path) -> dict[str, Any]:
    """Publikace pro volajícího, který už drží execution.lock tohoto běhu."""
    run_root = Path(run_dir).resolve()
    state_path = run_root / "run_state.json"
    if not state_path.is_file():
        raise OrchestrationError("PUBLISH_RUN_MISSING", str(run_root))

    recovery = recover_publish_journal_already_locked(run_root)
    state = parse_json_strict(state_path.read_text(encoding="utf-8"))
    if recovery and recovery.get("status") == "committed":
        _apply_committed_report_to_state(state_path, state, recovery)
        _record_publication(run_root, recovery)
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
    report = commit_publish_already_locked(plan, run_dir=run_root)
    _apply_committed_report_to_state(state_path, state, report)
    _record_publication(run_root, report)
    return report
