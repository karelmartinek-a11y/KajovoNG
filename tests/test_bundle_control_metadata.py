"""Metadata řídicích souborů nejsou nové důkazní artefakty Run Bundle."""

import struct
from pathlib import Path

import pytest

from kajovo.core.run_bundle import RunBundle


def apple_double():
    return struct.pack(">II16sHIII", 0x00051607, 0x00020000, b"Mac OS X        ", 1, 9, 38, 4) + b"meta"


@pytest.mark.parametrize("control", ["checksums.json", "bundle.json", "execution.lock"])
def test_control_metadata_created_after_sealing_preserves_integrity(tmp_path, control):
    bundle = RunBundle(tmp_path / "RUN_METADATA", "RUN_METADATA", create=True)
    bundle.seal()
    manifest = bundle.checksums_path.read_bytes()
    sidecar = bundle.root / ("._" + control)
    raw = apple_double()
    sidecar.write_bytes(raw)
    assert bundle.verify_integrity()["valid"]
    assert sidecar.read_bytes() == raw
    assert bundle.checksums_path.read_bytes() == manifest


@pytest.mark.parametrize("raw", [b"not metadata", apple_double()[:30],
                                apple_double().replace(struct.pack(">I", 9), struct.pack(">I", 1))])
def test_control_name_does_not_hide_invalid_metadata_or_data_fork(tmp_path, raw):
    bundle = RunBundle(tmp_path / "RUN_FALSE_METADATA", "RUN_FALSE_METADATA", create=True)
    bundle.seal()
    (bundle.root / "._checksums.json").write_bytes(raw)
    assert not bundle.verify_integrity()["valid"]


def test_application_sidecar_remains_part_of_sealed_evidence(tmp_path):
    bundle = RunBundle(tmp_path / "RUN_REAL_EVIDENCE", "RUN_REAL_EVIDENCE", create=True)
    sidecar = bundle.root / "._request.json"
    sidecar.write_bytes(apple_double())
    bundle.seal()
    sidecar.write_bytes(b"changed")
    result = bundle.verify_integrity()
    assert not result["valid"]
    assert any("Hash nesouhlasí: ._request.json" in error for error in result["errors"])


def test_output_manifest_reader_preserves_and_skips_system_metadata(tmp_path):
    from kajovo.core.runlog import RunLogger, load_output_evidence
    logger = RunLogger(str(tmp_path), "RUN_MANIFEST_METADATA")
    path = Path(logger.paths.run_dir) / "manifests" / "._manifest.json"
    raw = apple_double()
    path.write_bytes(raw)
    assert load_output_evidence(logger.paths.run_dir) == []
    assert path.read_bytes() == raw
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        load_output_evidence(logger.paths.run_dir)


def test_validation_reader_skips_only_valid_system_metadata(tmp_path):
    from kajovo.core.run_bundle import LegacyRunAdapter
    bundle = RunBundle(tmp_path / "RUN_VALIDATION_METADATA", "RUN_VALIDATION_METADATA", create=True)
    path = bundle.root / "validations" / "._validation.json"
    path.write_bytes(apple_double())
    adapter = LegacyRunAdapter(bundle.root)
    assert adapter.validations() == []
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        adapter.validations()


def test_converter_counts_data_files_and_preserves_system_metadata(tmp_path):
    from utf8nobom.app import TargetSpec, build_scan_plan
    text = tmp_path / "text.txt"
    text.write_bytes(b"abc")
    metadata = tmp_path / "._text.txt"
    raw = apple_double()
    metadata.write_bytes(raw)
    plan = build_scan_plan([TargetSpec(tmp_path)])
    assert [task.path for task in plan.file_tasks] == [text]
    assert plan.total_units == 9
    assert metadata.read_bytes() == raw
    metadata.write_bytes(b"ordinary")
    assert {task.path for task in build_scan_plan([TargetSpec(tmp_path)]).file_tasks} == {text, metadata}


def test_photo_folder_import_skips_only_system_metadata(qtbot, tmp_path, monkeypatch):
    from PIL import Image
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.photos import PhotosPage
    Image.new("RGB", (8, 6)).save(tmp_path / "input.png")
    path = tmp_path / "._input.png"
    path.write_bytes(apple_double())
    context = StudioContext(AppSettings(log_dir=str(tmp_path / "LOG")), Operations(None))
    page = PhotosPage(context)
    context.operations.setParent(page)
    qtbot.addWidget(page)
    monkeypatch.setattr("kajovo.studio.photos.get_existing_directory", lambda *args: str(tmp_path))
    page.pick_folder()
    assert page.photos.count() == 1
    assert page.notice.text() == "Fotografií připravených k úpravě: 1"
    path.write_bytes(b"invalid image")
    page.pick_folder()
    assert "Fotografií připravených" not in page.notice.text()
