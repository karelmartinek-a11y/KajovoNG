"""Souborové hranice dodání: snapshoty, atomický zápis a filtry vstupu."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core import filescan, utils
from kajovo.core.runs import delivery_execution


@pytest.mark.parametrize("log_failure", [False, True])
def test_snapshot_keeps_project_bytes_excludes_runtime_and_never_overwrites(
    tmp_path, monkeypatch, caplog, log_failure,
):
    root = tmp_path / "project"
    root.mkdir()
    content = b"print('puvodni')\r\n"
    (root / "main.py").write_bytes(content)
    for relative in (
        ".venv/runtime.txt", "venv/runtime.txt", "LOG/run.txt",
        "project010120260000/old.txt", "src/.venv/nested.txt", "src/module.py",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative, encoding="utf-8")
    monkeypatch.setattr(
        delivery_execution, "time", SimpleNamespace(strftime=lambda _fmt: "19092026120000"),
    )
    logger = Mock()
    if log_failure:
        logger.event.side_effect = OSError("evidence není zapisovatelná")
    worker = SimpleNamespace(log=logger)

    snapshot = Path(delivery_execution._create_snapshot(worker, str(root)))

    assert snapshot == root / "project19092026120000"
    assert sorted(path.relative_to(snapshot).as_posix() for path in snapshot.rglob("*") if path.is_file()) == [
        "main.py", "src/module.py",
    ]
    assert (snapshot / "main.py").read_bytes() == content
    assert (snapshot / "src/module.py").read_text(encoding="utf-8") == "src/module.py"
    assert (root / ".venv/runtime.txt").is_file()
    assert (root / "project010120260000/old.txt").is_file()
    logger.event.assert_called_once_with("versing.snapshot.created", {"snap_dir": str(snapshot)})
    if log_failure:
        assert "Zápis pomocné evidence selhal" in caplog.text

    (root / "main.py").write_bytes(b"nova prace\n")
    with pytest.raises(FileExistsError):
        delivery_execution._create_snapshot(worker, str(root))
    assert (snapshot / "main.py").read_bytes() == content
    assert (root / "main.py").read_bytes() == b"nova prace\n"
    assert logger.event.call_count == 1


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_atomic_write_preserves_original_and_removes_temporary_file(
    tmp_path, monkeypatch, operation,
):
    target = tmp_path / "existing.txt"
    original = "Původní práce\r\n".encode("utf-8")
    target.write_bytes(original)
    failure = Mock(side_effect=OSError("simulovaná chyba disku"))
    monkeypatch.setattr(utils.os, operation, failure)

    with pytest.raises(OSError, match="simulovaná chyba disku"):
        utils.atomic_write_text(str(target), "Nový obsah\n")

    failure.assert_called_once()
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("limit", [0, 3, 100])
def test_bounded_hash_matches_exact_byte_prefix_and_handles_eof(tmp_path, limit):
    target = tmp_path / "input.bin"
    original = "žluťoučký\r\n".encode("utf-8")
    target.write_bytes(original)

    assert utils.sha256_file(str(target), max_bytes=limit) == hashlib.sha256(original[:limit]).hexdigest()
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"allow_globs": ["**/*.py"]}, "not_in_allow_globs"),
        ({"deny_globs": ["**/*.TXT"]}, "deny_glob"),
        ({"allow_exts": [".PY"]}, "ext_not_allowed"),
        ({"deny_exts": [".txt"]}, "denied_extension"),
        ({"max_size_bytes": 3}, "too_large"),
    ],
)
def test_scan_filters_rejected_files_before_hashing_and_keeps_allowed_files(
    tmp_path, monkeypatch, options, reason,
):
    rejected = tmp_path / "blocked.TXT"
    rejected.write_bytes(b"blocked text\n")
    allowed = tmp_path / "allowed.py"
    allowed.write_bytes(b"ok\n")
    settings = dict(deny_dirs=[], deny_exts=None, allow_exts=None, deny_globs=None, allow_globs=None)
    settings.update(options)
    hasher = Mock(wraps=filescan.sha256_file)
    monkeypatch.setattr(filescan, "sha256_file", hasher)

    rows = filescan.scan_tree(str(tmp_path), tmp_path.name, **settings)

    assert [row.rel_path for row in rows] == ["allowed.py", "blocked.TXT"]
    good, bad = rows
    assert good.uploadable and good.reason == "ok"
    assert good.sha256 == hashlib.sha256(b"ok\n").hexdigest()
    assert not bad.uploadable and bad.reason == reason
    assert bad.sha256 is None and bad.size == len(b"blocked text\n")
    hasher.assert_called_once_with(str(allowed))
    assert rejected.read_bytes() == b"blocked text\n"
