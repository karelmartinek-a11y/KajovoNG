from unittest.mock import Mock

import pytest

from kajovo.core.config import AppSettings
from kajovo.core.contracts import ContractError, validate_chunk_metadata
from kajovo.ui.batch_panel import BatchPanel


def chunk(index, count, more, following):
    return {"chunk_index": index, "chunk_count": count, "has_more": more, "next_chunk_index": following}


@pytest.mark.parametrize("metadata", [chunk(0, 3, False, None), chunk(0, 1, True, 1),
                                     chunk(0, True, False, None), chunk(0, 10**12, False, None),
                                     chunk(0, 1, False, 1), chunk(0, 0, True, True)])
def test_inconsistent_chunk_metadata_is_rejected(metadata):
    with pytest.raises(ContractError):
        validate_chunk_metadata(metadata)


def test_batch_contradictory_terminal_chunks_preserve_existing_file(tmp_path, qtbot):
    panel = BatchPanel(AppSettings(), "")
    qtbot.addWidget(panel)
    target = tmp_path / "keep.txt"
    target.write_text("original", encoding="utf-8")
    parts, written, errors = {}, [], []
    for index in (0, 1):
        panel._apply_contract_payload({"contract": "A3_FILE", "path": "keep.txt", "content": "partial",
                                       "chunking": chunk(index, 0, False, None)}, str(tmp_path), written, parts, errors)
    panel._finalize_chunked_files(parts, str(tmp_path), written, errors)
    assert errors and not written
    assert target.read_text(encoding="utf-8") == "original"


def test_batch_accepts_complete_out_of_order_chunks(tmp_path, qtbot):
    panel = BatchPanel(AppSettings(), "")
    qtbot.addWidget(panel)
    parts, written, errors = {}, [], []
    for index in (1, 0):
        panel._apply_contract_payload({"contract": "A3_FILE", "path": "result.txt", "content": str(index),
                                       "chunking": chunk(index, 2, index == 0, 1 if index == 0 else None)},
                                      str(tmp_path), written, parts, errors)
    panel._finalize_chunked_files(parts, str(tmp_path), written, errors)
    assert not errors and len(written) == 1
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "01"


@pytest.mark.parametrize("bundle", [{"files": {}}, {"files": None}, {"files": [], "root": []}, {}])
def test_invalid_bundle_is_not_an_empty_success(bundle):
    with pytest.raises(ContractError):
        BatchPanel._write_files_from_bundle(Mock(), bundle, "unused")


def test_batch_second_bundle_cannot_overwrite_first_result(tmp_path, qtbot):
    panel = BatchPanel(AppSettings(), "")
    qtbot.addWidget(panel)
    parts, written, errors = {}, [], []
    first = {"contract": "C_FILES_ALL", "files": [{"path": "file.txt", "content": "first"}]}
    panel._apply_contract_payload(first, str(tmp_path), written, parts, errors)
    second = {"contract": "C_FILES_ALL", "files": [{"path": "FILE.txt", "content": "second"}]}
    with pytest.raises(ContractError):
        panel._apply_contract_payload(second, str(tmp_path), written, parts, errors)
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "first"


def test_batch_case_colliding_chunks_are_rejected_before_any_write(tmp_path, qtbot):
    panel = BatchPanel(AppSettings(), "")
    qtbot.addWidget(panel)
    parts, written, errors = {}, [], []
    for name in ("file.txt", "FILE.txt"):
        panel._apply_contract_payload({"contract": "A3_FILE", "path": name, "content": "data",
                                       "chunking": chunk(0, 1, False, None)}, str(tmp_path), written, parts, errors)
    panel._finalize_chunked_files(parts, str(tmp_path), written, errors)
    assert errors and not written
    assert not (tmp_path / "file.txt").exists()


def test_missing_batch_contract_is_an_error(qtbot):
    panel = BatchPanel(AppSettings(), "")
    qtbot.addWidget(panel)
    errors = []
    panel._apply_contract_payload({}, "unused", [], {}, errors)
    assert errors
