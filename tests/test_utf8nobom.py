from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from utf8nobom.app import (
    RunLogger,
    TargetSpec,
    build_scan_plan,
    copy_directory_for_backup,
    normalize_text_bytes,
    repair_mojibake_text,
    rewrite_zip_if_needed,
    validate_input_paths,
)


class DummyTracker:
    def __init__(self) -> None:
        self.events: list[tuple[int, str, str]] = []

    def advance(self, units: int, phase: str, detail: str) -> None:
        self.events.append((units, phase, detail))


class Utf8NoBomTests(unittest.TestCase):
    def test_repair_mojibake_text(self) -> None:
        broken = "Příliš žluťoučký kůň".encode("utf-8").decode("latin1")
        repaired = repair_mojibake_text(broken)
        self.assertEqual("Příliš žluťoučký kůň", repaired)

    def test_normalize_text_bytes_removes_bom(self) -> None:
        raw = b"\xef\xbb\xbfAhoj svete\r\n"
        normalized, changed = normalize_text_bytes(raw)
        self.assertTrue(changed)
        self.assertEqual("Ahoj svete\n".encode("utf-8"), normalized)

    def test_rewrite_zip_if_needed_repairs_text_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            zip_path = root / "sample.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("note.txt", "Příliš".encode("utf-8").decode("latin1").encode("utf-8"))
                archive.writestr("bin.dat", b"\x00\x01\x02")

            tracker = DummyTracker()
            logger = RunLogger(root, "test")
            processed, changed = rewrite_zip_if_needed(zip_path, tracker, logger)

            self.assertEqual(2, processed)
            self.assertEqual(1, changed)
            with zipfile.ZipFile(zip_path, "r") as archive:
                self.assertEqual("Příliš", archive.read("note.txt").decode("utf-8"))
                self.assertEqual(b"\x00\x01\x02", archive.read("bin.dat"))


if __name__ == "__main__":
    unittest.main()


def test_utf8_backup_never_overwrites_existing_backup(tmp_path):
    source, backup = tmp_path / "source", tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    (backup / "original.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        copy_directory_for_backup(source, backup)
    assert (backup / "original.txt").read_text() == "keep"


def test_utf8_plan_skips_git_and_deduplicates_nested_targets(tmp_path):
    source, backup = tmp_path / "source", tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    nested = source / "nested"
    nested.mkdir()
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("metadata")
    (nested / "file.txt").write_text("content")
    targets, _ = validate_input_paths([str(nested), str(source)], str(backup))
    assert targets == [TargetSpec(source)]
    assert [task.path for task in build_scan_plan(targets).file_tasks] == [nested / "file.txt"]


def test_zip_preserves_comment_and_rejects_unsafe_paths_without_changes(tmp_path):
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.comment = b"important metadata"
        archive.writestr("file.txt", b"\xef\xbb\xbftext")
    rewrite_zip_if_needed(path, Mock(), RunLogger(tmp_path, "safe"))
    with zipfile.ZipFile(path) as archive:
        assert archive.comment == b"important metadata"
        assert archive.read("file.txt") == b"text"
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("../unsafe.txt", b"data")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        rewrite_zip_if_needed(path, Mock(), RunLogger(tmp_path, "unsafe"))
    assert path.read_bytes() == original
