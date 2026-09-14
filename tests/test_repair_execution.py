"""Změna skriptu po schválení nesmí spustit jiný obsah."""

from unittest.mock import Mock

import pytest

from kajovo.core.repair_execution import execute_repair, prepare_repair


def test_changed_repair_is_refused_before_process(tmp_path, monkeypatch):
    (tmp_path / "readmerepair.txt").write_text("Kontrola", encoding="utf-8")
    path = tmp_path / "run_this_script_repairme_kajovo_windows.bat"
    path.write_bytes(b"echo approved")
    proposal = prepare_repair(tmp_path)
    path.write_bytes(b"echo changed")
    runner = Mock()
    monkeypatch.setattr("kajovo.core.repair_execution.subprocess.run", runner)
    with pytest.raises(ValueError, match="změnil"):
        execute_repair(proposal, Mock())
    runner.assert_not_called()
