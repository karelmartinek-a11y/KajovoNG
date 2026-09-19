"""Zmrazení schválených zdrojů do SOURCE_PACK_V1."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..filescan import scan_tree
from ..utils import sha256_file
from .contracts import canonical_sha256
from .errors import OrchestrationError


@dataclass(frozen=True)
class SourceSegment:
    id: str
    start_byte: int
    end_byte: int
    sha256: str
    classification: str = "unclassified"


@dataclass(frozen=True)
class FrozenSource:
    id: str
    kind: str
    sha256: str
    byte_length: int
    media_type: str
    segments: tuple[SourceSegment, ...]


@dataclass(frozen=True)
class SourcePack:
    version: int
    pack_id: str
    sources: tuple[FrozenSource, ...]
    scope_revision: int = 1

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "pack_id": self.pack_id,
            "sources": [
                {
                    **{k: v for k, v in asdict(source).items() if k != "segments"},
                    "segments": [asdict(segment) for segment in source.segments],
                }
                for source in self.sources
            ],
            "scope_revision": self.scope_revision,
        }

    @property
    def hash(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class ApprovedInput:
    source_id: str
    kind: str
    data: bytes
    media_type: str
    classification: str = "unclassified"
    label: str = ""


def segment_text_bytes(
    data: bytes,
    max_bytes: int = 65536,
    *,
    prefix: str = "SEG",
    classification: str = "unclassified",
) -> tuple[SourceSegment, ...]:
    if max_bytes <= 0:
        raise OrchestrationError("SOURCE_SEGMENT_LIMIT", "Segment limit musí být kladný.")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OrchestrationError("SOURCE_ENCODING", "Textový zdroj není validní UTF-8.") from exc
    if not data:
        return ()
    out: list[SourceSegment] = []
    start = 0
    while start < len(data):
        end = min(len(data), start + max_bytes)
        if end < len(data):
            while end > start:
                try:
                    data[start:end].decode("utf-8", errors="strict")
                    break
                except UnicodeDecodeError:
                    end -= 1
            if end == start:
                raise OrchestrationError("SOURCE_ENCODING", "Nelze najít bezpečnou UTF-8 hranici.")
        chunk = data[start:end]
        out.append(
            SourceSegment(
                id=f"{prefix}-{len(out) + 1:04d}",
                start_byte=start,
                end_byte=end,
                sha256=hashlib.sha256(chunk).hexdigest(),
                classification=classification,
            )
        )
        start = end
    if out[0].start_byte != 0 or out[-1].end_byte != len(data):
        raise OrchestrationError("SOURCE_COVERAGE", "Segmenty nepokrývají celý zdroj.")
    return tuple(out)


def freeze_sources(inputs: Sequence[ApprovedInput], store=None) -> SourcePack:
    frozen: list[FrozenSource] = []
    for index, item in enumerate(inputs, 1):
        if item.kind not in {"user_text", "file", "image", "diagnostic", "existing_project"}:
            raise OrchestrationError("SOURCE_KIND", f"Neznámý druh zdroje: {item.kind}")
        digest = hashlib.sha256(item.data).hexdigest()
        segments: tuple[SourceSegment, ...] = ()
        if item.media_type.startswith("text/") or item.kind == "user_text":
            segments = segment_text_bytes(
                item.data,
                prefix=f"SEG-{index:04d}",
                classification=item.classification,
            )
        source = FrozenSource(
            id=item.source_id or f"SRC-{index:04d}",
            kind=item.kind,
            sha256=digest,
            byte_length=len(item.data),
            media_type=item.media_type or "application/octet-stream",
            segments=segments,
        )
        frozen.append(source)
        if store is not None and hasattr(store, "put_bytes"):
            store.put_bytes(source.id, item.data, {"sha256": digest, "label": item.label})
    payload = {
        "version": 1,
        "sources": [
            {
                **{k: v for k, v in asdict(source).items() if k != "segments"},
                "segments": [asdict(segment) for segment in source.segments],
            }
            for source in frozen
        ],
        "scope_revision": 1,
    }
    return SourcePack(1, "SOURCE-PACK-" + canonical_sha256(payload)[:24], tuple(frozen), 1)


def freeze_run_sources(cfg, settings, log) -> SourcePack:
    """Zmrazí lokálně dostupný scope před prvním placeným requestem."""
    inputs: list[ApprovedInput] = []
    prompt = str(getattr(cfg, "prompt", "") or "")
    inputs.append(
        ApprovedInput(
            "SRC-USER-TEXT",
            "user_text",
            prompt.encode("utf-8"),
            "text/plain",
            "normative",
            "run prompt",
        )
    )
    root = str(getattr(cfg, "in_dir", "") or "").strip()
    if root:
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise OrchestrationError("SOURCE_ROOT_MISSING", "Vstupní projekt není dostupný.")
        policy = settings.security
        items = scan_tree(
            str(root_path),
            root_path.name,
            [".git", "venv", ".venv", "LOG", "cache", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"],
            policy.deny_extensions_in,
            policy.allow_extensions_in,
            policy.deny_globs_in,
            policy.allow_globs_in,
            allow_sensitive=policy.allow_upload_sensitive,
        )
        for item in items:
            path = Path(item.abs_path)
            if not path.is_file() or path.is_symlink():
                continue
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise OrchestrationError(
                    "SOURCE_CHANGED_DURING_FREEZE",
                    f"Zdroj se změnil během zmrazení: {item.rel_path}",
                )
            digest = hashlib.sha256(data).hexdigest()
            if item.sha256 and item.sha256 != digest:
                raise OrchestrationError(
                    "SOURCE_CHANGED_DURING_FREEZE",
                    f"Hash zdroje se změnil během zmrazení: {item.rel_path}",
                )
            media = mimetypes.guess_type(item.rel_path)[0] or "application/octet-stream"
            inputs.append(
                ApprovedInput(
                    f"SRC-PROJECT-{len(inputs):05d}",
                    "existing_project",
                    data,
                    media,
                    "context",
                    item.rel_path,
                )
            )
            try:
                log.bundle.archive_artifact(
                    path,
                    role="in_project_file",
                    kind="source_pack_input",
                    reconstruction_role=item.rel_path,
                    metadata={"relative_path": item.rel_path, "sha256": digest},
                )
            except (OSError, ValueError):
                raise
    pack = freeze_sources(inputs)
    log.save_json("manifests", "source_pack_v1", pack.to_dict())
    log.update_state({"source_pack": pack.to_dict(), "source_pack_hash": pack.hash})
    return pack


def source_by_ref(pack: SourcePack, source_id: str, segment_id: str) -> tuple[FrozenSource, SourceSegment]:
    source = next((item for item in pack.sources if item.id == source_id), None)
    if source is None:
        raise OrchestrationError("SOURCE_REF_UNKNOWN", source_id)
    segment = next((item for item in source.segments if item.id == segment_id), None)
    if segment is None:
        raise OrchestrationError("SOURCE_REF_UNKNOWN", segment_id)
    return source, segment
