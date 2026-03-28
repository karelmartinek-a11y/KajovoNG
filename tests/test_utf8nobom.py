from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from utf8nobom.app import RunLogger, normalize_text_bytes, repair_mojibake_text, rewrite_zip_if_needed


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
