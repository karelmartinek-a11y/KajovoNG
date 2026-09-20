import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from kajovo.core.contracts import ContractError, validate_paths
from kajovo.core.filescan import is_probably_binary, match_any_glob, scan_tree
from kajovo.core.utils import atomic_write_text, safe_join_under_root


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


def test_atomic_write_failure_preserves_original(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("original", encoding="utf-8")
    with patch("kajovo.core.utils.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            atomic_write_text(str(path), "new")
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [path]
def test_sensitive_upload_requires_explicit_policy(tmp_path):
    from kajovo.core.filescan import scan_tree
    (tmp_path / ".env").write_text("password=synthetic-test-value", encoding="utf-8")
    args = (str(tmp_path), tmp_path.name, [], None, None, None, None)
    assert not scan_tree(*args)[0].uploadable
    allowed = scan_tree(*args, allow_sensitive=True)[0]
    assert allowed.uploadable and allowed.sensitive


def _legacy_policy_worker(run_dir, artifact):
    security = SimpleNamespace(
        deny_extensions_in=[],
        allow_extensions_in=None,
        deny_globs_in=[],
        allow_globs_in=None,
        allow_upload_sensitive=False,
    )
    log = SimpleNamespace(
        paths=SimpleNamespace(run_dir=str(run_dir)),
        bundle=SimpleNamespace(artifacts=lambda: [artifact]),
        save_json=Mock(),
    )
    return SimpleNamespace(
        log=log,
        settings=SimpleNamespace(security=security),
    )


def test_legacy_sourcepack_revalidates_current_policy_before_content_reuse(tmp_path):
    from kajovo.core.runs.attachments import _approved_project_items

    project = tmp_path / "project"
    project.mkdir()
    source = project / ".env"
    source.write_text("password=synthetic-test-value", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    run_dir = tmp_path / "RUN_LEGACY"
    frozen = run_dir / "files" / "legacy.env"
    frozen.parent.mkdir(parents=True)
    frozen.write_bytes(source.read_bytes())
    artifact = {
        "role": "in_project_file",
        "path_in_bundle": frozen.relative_to(run_dir).as_posix(),
        "reconstruction_role": ".env",
        "sha256": digest,
        "metadata": {
            "relative_path": ".env",
            "sha256": digest,
        },
    }
    worker = _legacy_policy_worker(run_dir, artifact)

    with pytest.raises(ContractError, match="odmítnut politikou"):
        _approved_project_items(worker, str(project))
    worker.log.save_json.assert_not_called()


def test_legacy_sourcepack_reapproval_is_metadata_only(tmp_path):
    from kajovo.core.runs.attachments import _approved_project_items

    project = tmp_path / "project"
    project.mkdir()
    source = project / "safe.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    run_dir = tmp_path / "RUN_LEGACY_SAFE"
    frozen = run_dir / "files" / "safe.py"
    frozen.parent.mkdir(parents=True)
    frozen.write_bytes(source.read_bytes())
    artifact = {
        "role": "in_project_file",
        "path_in_bundle": frozen.relative_to(run_dir).as_posix(),
        "reconstruction_role": "safe.py",
        "sha256": digest,
        "metadata": {
            "relative_path": "safe.py",
            "sha256": digest,
        },
    }
    worker = _legacy_policy_worker(run_dir, artifact)

    rows = _approved_project_items(worker, str(project))
    assert [row.rel_path for row in rows] == ["safe.py"]
    call = worker.log.save_json.call_args
    assert call.args[:2] == ("manifests", "legacy_source_reapproval_v1")
    evidence = call.args[2]
    assert evidence["content_included"] is False
    assert evidence["approved"][0]["sha256"] == digest
    assert "VALUE = 1" not in str(evidence)
