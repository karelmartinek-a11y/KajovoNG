"""Změna skriptu po schválení nesmí spustit jiný obsah."""

from unittest.mock import Mock
import subprocess
import sys

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


@pytest.mark.parametrize("remote", [True, False])
@pytest.mark.parametrize("code", [0, 1])
def test_repair_execution_records_process_result_and_removes_temp(tmp_path, monkeypatch, remote, code):
    if not remote and sys.platform != "win32":
        # Na jiné platformě ověřujeme explicitní odmítnutí Windows skriptu.
        code = 0
    (tmp_path / "readmerepair.txt").write_text("Popis", encoding="utf-8")
    name = "run_this_script_repairme_kajovo.sh" if remote else "run_this_script_repairme_kajovo_windows.bat"
    (tmp_path / name).write_bytes(b"echo offline")
    proposal = prepare_repair(tmp_path, remote=remote)
    result = subprocess.CompletedProcess(["offline"], code, "výstup", "chyba")
    runner = Mock(return_value=result)
    monkeypatch.setattr("kajovo.core.repair_execution.subprocess.run", runner)
    monkeypatch.setattr("kajovo.core.diagnostics.ssh.execute_ssh_repair", runner)
    if not remote and sys.platform != "win32":
        with pytest.raises(ValueError, match="Windows"):
            execute_repair(proposal, Mock())
        runner.assert_not_called()
        return
    if code:
        with pytest.raises(subprocess.CalledProcessError):
            execute_repair(proposal, Mock())
    else:
        assert execute_repair(proposal, Mock())["status"] == "completed"
    assert runner.call_count == 1
    assert len(list(tmp_path.glob("*.bat"))) == (0 if remote else 1)
    log = tmp_path / ("_repair_ssh_exec_log.txt" if remote else "_repair_exec_log.txt")
    assert "výstup" in log.read_text(encoding="utf-8")


def test_missing_and_empty_repair_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        prepare_repair(tmp_path)
    (tmp_path / "readmerepair.txt").write_text("Popis", encoding="utf-8")
    (tmp_path / "run_this_script_repairme_kajovo_windows.bat").write_bytes(b"")
    with pytest.raises(ValueError, match="prázdný"):
        prepare_repair(tmp_path)
