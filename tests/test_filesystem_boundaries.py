import hashlib
from pathlib import Path
import tempfile
import unittest

from kajovo.core.contracts import ContractError, validate_paths
from kajovo.core.filescan import scan_tree, match_any_glob, is_probably_binary
from kajovo.core.utils import safe_join_under_root


class FilesystemBoundaryTests(unittest.TestCase):
    def test_rejects_nonportable_and_escaping_paths(self):
        for path in ("../x", "/tmp/x", "C:/x", "C:x", "NUL.txt", "a/../x", "a.", "a ", "a:x", "a//x"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_join_under_root("output", path)

    def test_rejects_case_collisions(self):
        with self.assertRaises(ContractError):
            validate_paths([{"path": "A.py"}, {"path": "a.py"}])

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "out"
            root.mkdir()
            try:
                (root / "link").symlink_to(Path(tmp), target_is_directory=True)
            except OSError:
                self.skipTest("Prostředí nepovoluje vytváření symlinků.")
            with self.assertRaises(ValueError):
                safe_join_under_root(str(root), "link/escape.txt")

    def test_root_globs_and_czech_text(self):
        self.assertTrue(match_any_glob(".git/config", ["**/.git/**"]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text.txt"
            path.write_text("Žluťoučký kůň úpěl ďábelské ódy. " * 200, encoding="utf-8")
            self.assertFalse(is_probably_binary(str(path)))

    def test_scans_secrets_after_initial_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text.txt"
            path.write_text("a" * 21000 + "\npassword=private", encoding="utf-8")
            item = scan_tree(tmp, "project", [], [], None, [], None)[0]
            self.assertTrue(item.sensitive)
            self.assertFalse(item.uploadable)

    def test_hashes_entire_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = b"a" * (5 * 1024 * 1024 + 10)
            (Path(tmp) / "text.txt").write_bytes(data)
            item = scan_tree(tmp, "project", [], [], None, [], None)[0]
            self.assertEqual(item.sha256, hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    unittest.main()
