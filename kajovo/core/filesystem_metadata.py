"""Rozpoznání systémových AppleDouble v2 metadat bez změny jejich bytes."""

from pathlib import Path
import struct


def is_appledouble_metadata(path: Path) -> bool:
    if not path.name.startswith("._") or path.is_symlink():
        return False
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(26)
        if len(header) != 26:
            return False
        magic, version, _filler, count = struct.unpack(">II16sH", header)
        end = 26 + 12 * count
        if magic != 0x00051607 or version != 0x00020000 or not count or end > size:
            return False
        ids = set()
        ranges = []
        for _ in range(count):
            descriptor = stream.read(12)
            if len(descriptor) != 12:
                return False
            identifier, offset, length = struct.unpack(">III", descriptor)
            if identifier in ids or identifier in {0, 1} or offset < end or offset + length > size:
                return False
            ids.add(identifier)
            if length:
                ranges.append((offset, offset + length))
        cursor = end
        for start, stop in sorted(ranges):
            if start < cursor:
                return False
            stream.seek(cursor)
            remaining = start - cursor
            while remaining:
                chunk = stream.read(min(remaining, 65536))
                if not chunk or any(chunk):
                    return False
                remaining -= len(chunk)
            cursor = stop
        stream.seek(cursor)
        remaining = size - cursor
        while remaining:
            chunk = stream.read(min(remaining, 65536))
            if not chunk or any(chunk):
                return False
            remaining -= len(chunk)
    return True
