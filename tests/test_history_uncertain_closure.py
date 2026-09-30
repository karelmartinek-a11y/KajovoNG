"""Neurčité přijetí po pádu procesu nesmí Historie obejít starším checkpointem."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.studio.history_launcher import HistoryBranchLauncher
from test_runtime_end_to_end import ROOT, child


def verify_uncertain(root, relation):
    adapter = LegacyRunAdapter(root / 'LOG' / 'RUN_FOREGROUND')
    before = {str(p.relative_to(adapter.root)): p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    launcher = HistoryBranchLauncher(SimpleNamespace())
    checkpoint = next(row for row in adapter.checkpoints() if row['checkpoint_type'] == 'input_ready')
    with pytest.raises(ValueError, match='odeslání není potvrzen'):
        launcher.preview(adapter, checkpoint['checkpoint_id'], relation)
    assert before == {str(p.relative_to(adapter.root)): p.read_bytes() for p in adapter.root.rglob('*') if p.is_file()}
    calls = [json.loads(line) for line in (root / 'workflow-http.jsonl').read_text().splitlines()]
    assert sum(row['path'] == '/responses' for row in calls) == 1


@pytest.mark.parametrize('mode', ['QA', 'QFILE'])
@pytest.mark.parametrize('relation', ['continue', 'rerun', 'repair'])
def test_history_blocks_uncertain_foreground_submit_after_process_exit(tmp_path, mode, relation):
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'tests')]))
    script = "from pathlib import Path; import sys; from test_qa_qfile_http_closure import foreground_process; foreground_process(Path(sys.argv[1]), " + repr(mode) + ", 'dispatch')"
    first = subprocess.run([sys.executable, '-c', script, str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert first.returncode == 77, first.stdout + first.stderr
    child("from pathlib import Path; import sys; from test_history_uncertain_closure import verify_uncertain; verify_uncertain(Path(sys.argv[1]), " + repr(relation) + ")", tmp_path)
