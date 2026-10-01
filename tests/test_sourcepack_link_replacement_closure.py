"""Schválený SourcePack odmítne záměnu souboru či předka za interní symlink."""

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_workflows import make_worker

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration.source_pack import freeze_run_sources
from kajovo.core.runs.attachments import _approved_project_items


@pytest.mark.parametrize("replacement", ["file", "parent"])
def test_frozen_source_rejects_same_bytes_symlink_replacement(tmp_path, replacement):
    worker = make_worker(tmp_path, "MODIFY")
    root = tmp_path / "project"
    folder = root / "src"
    folder.mkdir(parents=True)
    source = folder / "asset.txt"
    source.write_bytes(b"schvaleny presny obsah\n")
    worker.cfg.in_dir = str(root)
    client = Mock()
    freeze_run_sources(worker.cfg, worker.settings, worker.log, client=client)
    approved = _approved_project_items(worker, str(root))
    assert len(approved) == 1
    original = Path(approved[0].frozen_path).read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if replacement == "file":
        retained = folder / "retained.txt"
        source.rename(retained)
        source.symlink_to(retained.name)
    else:
        retained = root / "retained"
        folder.rename(retained)
        folder.symlink_to(retained.name, target_is_directory=True)
    assert source.read_bytes() == original
    calls = list(client.mock_calls)
    with pytest.raises(ContractError, match="změnil typ"):
        _approved_project_items(worker, str(root))
    assert client.mock_calls == calls
    assert hashlib.sha256(Path(approved[0].frozen_path).read_bytes()).hexdigest() == digest
