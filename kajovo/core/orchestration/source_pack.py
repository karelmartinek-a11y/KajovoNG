"""Zmrazení schválených zdrojů do SOURCE_PACK_V1."""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ..filescan import scan_tree
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


def _remote_file_ids(cfg, client) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    def add(value) -> None:
        identifier = str(value or "").strip()
        if identifier and identifier not in seen:
            values.append(identifier)
            seen.add(identifier)

    for identifier in list(getattr(cfg, "input_file_ids", None) or []):
        add(identifier)
    for identifier in list(getattr(cfg, "attached_file_ids", None) or []):
        add(identifier)

    for vector_store_id in list(
        getattr(cfg, "attached_vector_store_ids", None) or []
    ):
        if client is None:
            raise OrchestrationError(
                "SOURCE_REMOTE_CLIENT_REQUIRED",
                "Připojený vector store nelze zmrazit bez read-only API klienta.",
            )
        try:
            rows = client.list_vector_store_files(vector_store_id)
        except Exception as exc:
            raise OrchestrationError(
                "SOURCE_VECTOR_STORE_UNAVAILABLE",
                f"Nelze načíst obsah vector store {vector_store_id}.",
            ) from exc
        if not isinstance(rows, list):
            raise OrchestrationError(
                "SOURCE_VECTOR_STORE_INVALID",
                f"Vector store {vector_store_id} vrátil neplatný seznam souborů.",
            )
        for row in rows:
            if not isinstance(row, dict):
                raise OrchestrationError(
                    "SOURCE_VECTOR_STORE_INVALID",
                    f"Vector store {vector_store_id} obsahuje neplatný file záznam.",
                )
            add(row.get("file_id") or row.get("id"))
    return values


def _freeze_remote_inputs(cfg, log, client, inputs: list[ApprovedInput]) -> None:
    identifiers = _remote_file_ids(cfg, client)
    if not identifiers:
        return
    if client is None:
        raise OrchestrationError(
            "SOURCE_REMOTE_CLIENT_REQUIRED",
            "Připojené Files ID nelze přesně zmrazit bez read-only API klienta.",
        )

    root = Path(log.paths.misc_dir) / "source_pack_remote"
    root.mkdir(parents=True, exist_ok=True)
    for index, file_id in enumerate(identifiers, 1):
        try:
            metadata = client.retrieve_file(file_id)
            data = client.file_content(file_id)
        except Exception as exc:
            raise OrchestrationError(
                "SOURCE_REMOTE_UNAVAILABLE",
                f"Nelze přesně zmrazit připojený soubor {file_id}.",
            ) from exc
        if not isinstance(metadata, dict) or not isinstance(data, (bytes, bytearray)):
            raise OrchestrationError(
                "SOURCE_REMOTE_INVALID",
                f"Připojený soubor {file_id} nemá platná metadata nebo bajty.",
            )
        binary = bytes(data)
        declared_size = metadata.get("bytes")
        if isinstance(declared_size, int) and not isinstance(declared_size, bool):
            if declared_size >= 0 and declared_size != len(binary):
                raise OrchestrationError(
                    "SOURCE_REMOTE_SIZE_MISMATCH",
                    f"Připojený soubor {file_id} změnil velikost během zmrazení.",
                )
        filename = str(metadata.get("filename") or file_id).strip() or file_id
        suffix = Path(filename).suffix.lower()
        media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        kind = "image" if media.startswith("image/") else "file"
        source_id = (
            "SRC-REMOTE-"
            + hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:20]
        )
        digest = hashlib.sha256(binary).hexdigest()
        local = root / f"{index:05d}_{digest[:16]}{suffix}"
        local.write_bytes(binary)
        log.bundle.archive_artifact(
            local,
            role="remote_input",
            kind="source_pack_input",
            reconstruction_role=filename,
            reusable=True,
            metadata={
                "source_id": source_id,
                "provider_file_id": file_id,
                "filename": filename,
                "sha256": digest,
                "media_type": media,
            },
        )
        inputs.append(
            ApprovedInput(
                source_id,
                kind,
                binary,
                media,
                "context",
                filename,
            )
        )


def freeze_run_sources(cfg, settings, log, client=None) -> SourcePack:
    """Zmrazí celý schválený scope před prvním placeným requestem."""
    inputs: list[ApprovedInput] = []
    prompt = str(getattr(cfg, "prompt", "") or "")
    prompt_bytes = prompt.encode("utf-8")
    prompt_source = Path(log.paths.misc_dir) / "source_pack_user_text.txt"
    prompt_source.write_bytes(prompt_bytes)
    log.bundle.archive_artifact(
        prompt_source,
        role="user_input",
        kind="source_pack_input",
        reconstruction_role="user_prompt",
        reusable=True,
        metadata={
            "source_id": "SRC-USER-TEXT",
            "sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "media_type": "text/plain",
        },
    )
    inputs.append(
        ApprovedInput(
            "SRC-USER-TEXT",
            "user_text",
            prompt_bytes,
            "text/plain",
            "normative",
            "run prompt",
        )
    )
    _freeze_remote_inputs(cfg, log, client, inputs)
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
            source_id = f"SRC-PROJECT-{len(inputs):05d}"
            inputs.append(
                ApprovedInput(
                    source_id,
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
                    metadata={
                        "source_id": source_id,
                        "relative_path": item.rel_path,
                        "sha256": digest,
                        "media_type": media,
                    },
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


def source_context(log, pack: SourcePack) -> dict:
    """Reconstruct exact text segments from immutable Run Bundle artifacts."""
    artifacts = log.bundle.artifacts()
    by_source: dict[str, dict] = {}
    for artifact in artifacts:
        metadata = artifact.get("metadata") or {}
        source_id = metadata.get("source_id")
        if isinstance(source_id, str) and source_id:
            by_source[source_id] = artifact

    segments: list[dict] = []
    image_slots: list[dict] = []
    root = Path(log.paths.run_dir).resolve()
    for source in pack.sources:
        artifact = by_source.get(source.id)
        if artifact is None:
            raise OrchestrationError(
                "SOURCE_ARTIFACT_MISSING",
                f"Zmrazený zdroj {source.id} nemá immutable artefakt.",
            )
        path_in_bundle = artifact.get("path_in_bundle")
        if not isinstance(path_in_bundle, str) or not path_in_bundle:
            raise OrchestrationError("SOURCE_ARTIFACT_MISSING", source.id)
        path = (root / path_in_bundle).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise OrchestrationError("SOURCE_ARTIFACT_ESCAPE", source.id) from exc
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source.sha256:
            raise OrchestrationError("SOURCE_HASH_MISMATCH", source.id)
        if source.segments:
            for segment in source.segments:
                chunk = data[segment.start_byte:segment.end_byte]
                if hashlib.sha256(chunk).hexdigest() != segment.sha256:
                    raise OrchestrationError("SOURCE_SEGMENT_HASH", segment.id)
                segments.append({
                    "source_id": source.id,
                    "segment_id": segment.id,
                    "start_byte": segment.start_byte,
                    "end_byte": segment.end_byte,
                    "sha256": segment.sha256,
                    "text": chunk.decode("utf-8", errors="strict"),
                })
        elif source.media_type.startswith("image/"):
            image_slots.append({
                "slot_id": source.id,
                "role": "source_image",
                "asset_hash": source.sha256,
            })
    return {"segments": segments, "image_slots": image_slots}
