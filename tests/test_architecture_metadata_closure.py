"""Architektonický audit rozlišuje metadata podle bytes, nikoli prefixu názvu."""
from test_metadata_overlap_closure import metadata
import test_architecture_contracts as architecture


def test_architecture_ignores_valid_metadata_without_decoding(tmp_path):
    path = tmp_path/'._worker.py'
    path.write_bytes(metadata([(9,38,4)], b'\xff\x80\xb0\xfe'))
    assert architecture._imports(path) == set()


def test_architecture_keeps_ordinary_named_source(tmp_path,monkeypatch):
    path = tmp_path/'._ordinary.py'
    path.write_text('from PySide6.QtCore import QThread\n', encoding="utf-8")
    monkeypatch.setattr(architecture,'ROOT',tmp_path)
    assert list(architecture._python_files('.')) == [path]
    assert architecture._violations(architecture._python_files('.'),('PySide6',)) == ['._ordinary.py -> PySide6.QtCore']
