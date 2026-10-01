"""Audity nesmějí vynechat běžný zdroj podle prefixu názvu."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from test_metadata_overlap_closure import metadata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import verify_progress_coverage as progress
import verify_semantic_flows as semantic


@pytest.mark.parametrize('sidecar', [False, True])
def test_semantic_contract_discovery_checks_named_source(tmp_path, monkeypatch, sidecar):
    source = tmp_path / 'kajovo' / '._request.py'
    source.parent.mkdir()
    source.write_bytes(metadata([(9, 38, 4)], b'\xff\x80\xb0\xfe') if sidecar else b'response_format("QA_ANSWER_V2")\n')
    monkeypatch.setattr(semantic, 'ROOT', tmp_path)
    report = semantic._response_format_sites()
    assert report['errors'] == []
    assert report['explicit'] == ([] if sidecar else [{'path': 'kajovo/._request.py', 'line': 1, 'contract': 'QA_ANSWER_V2'}])


@pytest.mark.parametrize('sidecar', [False, True])
def test_progress_audit_detects_named_source_with_unsupported_dialog(tmp_path, monkeypatch, sidecar):
    names = {'docs/progress/reference_inventory_182.json', 'kajovo/studio/operations.py', 'kajovo/studio/progress_dialog.py', 'kajovo/studio/progress_view.py'}
    names.update(owner for family in progress.REFERENCE_PROGRESS_FAMILIES for owner in family.owners)
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    source = tmp_path / 'kajovo/studio/._ordinary.py'
    source.write_bytes(metadata([(9, 38, 4)], b'\xff\x80\xb0\xfe') if sidecar else b'from PySide6.QtWidgets import QProgressDialog\n')
    output = tmp_path / 'report.json'
    monkeypatch.setattr(progress, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['audit', '--output', str(output)])
    assert progress.main() == (0 if sidecar else 1)
    errors = json.loads(output.read_text())['errors']
    assert errors == ([] if sidecar else [{'path': 'kajovo/studio/._ordinary.py', 'error': 'Produkční Studio znovu používá starý QProgressDialog.'}])
