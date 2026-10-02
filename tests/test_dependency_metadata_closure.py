"""Dependency audit kontroluje zdroje a rozpoznává skutečná metadata."""
import pytest

from test_metadata_overlap_closure import metadata
from tools import verify_dependency_contract as contract


def test_dependency_audit_ignores_valid_metadata_and_generated_build(tmp_path, monkeypatch):
    build = tmp_path / 'Build'
    build.mkdir()
    (build / '._start.sh').write_bytes(metadata([(9, 38, 4)], b'\xff\x80\xb0\xfe'))
    generated = build / 'lib'
    generated.mkdir()
    (generated / 'copied.sh').write_text('pip install obsolete\n', encoding="utf-8")
    monkeypatch.setattr(contract, 'ROOT', tmp_path)
    assert contract.ad_hoc_install_lines() == []


@pytest.mark.parametrize('name', ['._ordinary.sh', 'normal.sh'])
def test_dependency_audit_keeps_ordinary_scripts(tmp_path, monkeypatch, name):
    build = tmp_path / 'Build'
    build.mkdir()
    (build / name).write_text('pip install forbidden\n', encoding="utf-8")
    monkeypatch.setattr(contract, 'ROOT', tmp_path)
    assert contract.ad_hoc_install_lines() == [f'Build/{name}:1: pip install forbidden']
