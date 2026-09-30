"""AppleDouble nesmí ignorovat překryté ani připojené běžné bytes."""

import struct

import pytest

from kajovo.core.filesystem_metadata import is_appledouble_metadata
from kajovo.core.run_bundle import RunBundle


def metadata(entries, payload):
    return struct.pack(">II16sH", 0x00051607, 0x00020000, b"Mac OS X        ", len(entries)) + b"".join(struct.pack(">III", *row) for row in entries) + payload


@pytest.mark.parametrize("raw", [
    metadata([(9, 50, 8), (2, 50, 8)], b"metadata"),
    metadata([(9, 38, 4)], b"meta" + b'{"ordinary_content":true}'),
    metadata([(9, 42, 4)], b"DATAmeta"),
])
def test_malformed_metadata_is_not_exempt_from_bundle_integrity(tmp_path, raw):
    bundle = RunBundle(tmp_path / "RUN_METADATA_GUARD", "RUN_METADATA_GUARD", create=True)
    bundle.seal()
    sidecar = bundle.root / "._checksums.json"
    sidecar.write_bytes(raw)
    assert not is_appledouble_metadata(sidecar)
    assert not bundle.verify_integrity()["valid"]
    assert sidecar.read_bytes() == raw


def test_real_metadata_and_named_content_keep_distinct_integrity(tmp_path):
    raw = metadata([(9, 50, 32), (2, 82, 4)], b"\0" * 32 + b"rsrc")
    target = tmp_path / "._file"
    target.write_bytes(raw)
    assert is_appledouble_metadata(target)
    target.write_text("Běžný obsah ._file", encoding="utf-8")
    assert not is_appledouble_metadata(target)
    origin = tmp_path / "metadata"
    origin.write_bytes(raw)
    target.unlink()
    target.symlink_to(origin)
    assert not is_appledouble_metadata(target)
