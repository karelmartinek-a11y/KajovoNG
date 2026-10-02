"""Procesní zámek musí odmítnout druhého vlastníka a přežít ukončení vlastníka bez blokace."""
import os
import subprocess
import sys

from test_runtime_end_to_end import ROOT, child


def test_execution_lock_two_processes_and_killed_owner(tmp_path):
    environment = dict(os.environ, PYTHONPATH=str(ROOT))
    code = "from pathlib import Path; import sys; from kajovo.core.runs.locking import ExecutionLock; lock=ExecutionLock(Path(sys.argv[1])/'execution.lock'); assert lock.acquire(); print('LOCKED', flush=True); sys.stdin.readline(); lock.release()"
    first = subprocess.Popen([sys.executable,'-c',code,str(tmp_path)],env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert first.stdout.readline().strip() == 'LOCKED'
        child("from pathlib import Path; import sys; from kajovo.core.runs.locking import ExecutionLock; assert not ExecutionLock(Path(sys.argv[1])/'execution.lock').acquire()",tmp_path)
        first.kill()
        exit_code = first.wait(timeout=10)
        assert exit_code == 1 if sys.platform == "win32" else exit_code < 0
        child("from pathlib import Path; import sys; from kajovo.core.runs.locking import ExecutionLock; lock=ExecutionLock(Path(sys.argv[1])/'execution.lock'); assert lock.acquire(); lock.release(); assert lock.acquire(); lock.release()",tmp_path)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)
        for stream in [first.stdin,first.stdout,first.stderr]:
            stream.close()
