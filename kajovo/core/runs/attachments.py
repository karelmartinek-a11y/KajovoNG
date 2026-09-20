from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from ..compat import (
    SUPPORTED_INPUT_FILE_EXTS,
    SUPPORTED_INPUT_IMAGE_EXTS,
    validate_input_file_sizes,
)
from ..contracts import (
    ContractError,
)
from ..openai_client import OpenAIClient
from ..openai_transport import OpenAIError, SubmissionOutcomeUnknown
from ..progress import ProgressEvent
from ..structured_output import (
    text_format,
)
from ..utils import ensure_dir, safe_join_under_root, sha256_file, ts_code
from ..orchestration.source_pack import (
    project_binary_asset_candidate,
    validate_project_binary_asset,
)
from .observability import record_event
from .polling import VectorStorePollingContext, wait_vector_store_files

if TYPE_CHECKING:
    from .context import RunContext

def _attachments_snapshot(self: RunContext,
    stage: str,
    ref_file_ids: list[str],
    input_file_ids: list[str],
    input_image_ids: list[str],
    vector_store_ids: list[str],
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    ts = self._ts()
    ref_ids = [fid for fid in (ref_file_ids or []) if fid]
    input_ids = [fid for fid in (input_file_ids or []) if fid]
    image_ids = [fid for fid in (input_image_ids or []) if fid]
    vs_ids = [vid for vid in (vector_store_ids or []) if vid]
    tool_types = [t.get("type") for t in (tools or []) if isinstance(t, dict)]
    return {
        "ts": ts,
        "stage": stage,
        "file_ids": ref_ids,
        "input_file_ids": input_ids,
        "input_image_ids": image_ids,
        "vector_store_ids": vs_ids,
        "tool_types": tool_types,
        "use_file_search": bool(self.cfg.use_file_search),
        "supports_file_search": bool(self.cfg.model_caps.get("supports_file_search", False)),
        "supports_vector_store": bool(self.cfg.model_caps.get("supports_vector_store", False)),
    }


def _input_file_ids(self: RunContext) -> list[str]:
    if hasattr(self.cfg, "input_file_ids"):
        return list(self.cfg.input_file_ids or [])
    return list(self.cfg.attached_file_ids or [])


def _generate_model(self: RunContext, step: str) -> str:
    default_model = str(self.cfg.model or "").strip()
    key = str(step or "").strip().upper()
    if key == "A1":
        chosen = str(getattr(self.cfg, "model_a1", "") or "").strip()
    elif key == "A2":
        chosen = str(getattr(self.cfg, "model_a2", "") or "").strip()
    elif key == "A3":
        chosen = str(getattr(self.cfg, "model_a3", "") or "").strip()
    else:
        chosen = ""
    return chosen or default_model


def _model_caps(self: RunContext, model):
    return self.cfg.model_caps if model == self.cfg.model else (self.cfg.caps_by_model or {}).get(model, {})


def _preparation_cap(self: RunContext, name):
    if self.cfg.mode == "GENERATE":
        return all(self._model_caps(self._generate_model(step)).get(name, False) for step in ("A1", "A2"))
    return bool(self.cfg.model_caps.get(name, False))


def _remember_file_name(self: RunContext, file_id: str, name: str) -> None:
    fid = str(file_id or "").strip()
    if not fid:
        return
    filename = str(name or "").strip()
    if filename:
        self._file_name_cache[fid] = filename
    ext = os.path.splitext(filename)[1].lower()
    if ext in SUPPORTED_INPUT_IMAGE_EXTS:
        self._input_kind_cache[fid] = "input_image"
    elif ext in SUPPORTED_INPUT_FILE_EXTS:
        self._input_kind_cache[fid] = "input_file"
    else:
        self._input_kind_cache[fid] = "unsupported"


def _classify_input_kind(self: RunContext, client: OpenAIClient, file_id: str) -> str:
    fid = str(file_id or "").strip()
    if not fid:
        return "unsupported"
    cached = self._input_kind_cache.get(fid)
    if cached:
        return cached
    filename = self._file_name_cache.get(fid, "")
    if filename:
        self._remember_file_name(fid, filename)
        return self._input_kind_cache.get(fid, "unsupported")
    try:
        meta = client.retrieve_file(fid)
        filename = str(meta.get("filename") or "").strip()
        self._remember_file_name(fid, filename)
    except (OpenAIError, ValueError, TypeError, AttributeError) as e:
        raise ContractError(f"Nelze ověřit připojený soubor {fid}.") from e
    kind = self._input_kind_cache.get(fid, "unsupported")
    if kind == "unsupported":
        shown = filename or "(unknown filename)"
        self._log_debug(f"Unsupported input attachment type for {fid} ({shown}); keeping only reference.")
    return kind


def _log_request_attachments(self: RunContext,
    stage: str,
    ref_file_ids: list[str],
    input_file_ids: list[str],
    input_image_ids: list[str],
    vector_store_ids: list[str],
    tools: list[dict[str, Any]] | None,
) -> None:
    snapshot = self._attachments_snapshot(stage, ref_file_ids, input_file_ids, input_image_ids, vector_store_ids, tools)
    record_event(self.log, "request.attachments", snapshot)
    if snapshot.get("file_ids") or snapshot.get("input_file_ids") or snapshot.get("input_image_ids") or snapshot.get("vector_store_ids") or snapshot.get("tool_types"):
        self._log_debug(
            f"{stage}: attachments files={len(snapshot.get('file_ids') or [])} input_files={len(snapshot.get('input_file_ids') or [])} "
            f"input_images={len(snapshot.get('input_image_ids') or [])} vector_stores={len(snapshot.get('vector_store_ids') or [])} "
            f"tools={','.join(snapshot.get('tool_types') or []) or 'none'}"
        )


def _prepare_response_runtime(self: RunContext, client):
    if self._input_file_ids():
        self._set(2, 0, "Ověřuji vstupní přílohy…", stage="Přílohy")
    input_files, input_images = self._build_input_attachments(client, self._input_file_ids())
    if input_files or input_images:
        for model in (self.cfg.caps_by_model or {}):
            client.validate_access({"model": model, "text": text_format(),
                "input": self._input_parts("kontrola příloh", input_files, input_images)})
    diag_file_ids, diag_text = self._maybe_collect_diagnostics(client)
    self._diag_text = diag_text or ""
    self._in_dir_info = self._prepare_in_dir_upload(client)
    self._vector_store_ids = list(self.cfg.attached_vector_store_ids or [])
    if self._in_dir_info and self._in_dir_info.get("vector_store_id"):
        self._vector_store_ids.append(str(self._in_dir_info["vector_store_id"]))
    if diag_file_ids:
        self._attach_diagnostics_vector_store(client, diag_file_ids)
    if (
        self._preparation_cap("supports_file_search")
        and (bool(self.cfg.use_file_search) or bool(diag_file_ids))
        and self._vector_store_ids
    ):
        uniq: list[str] = []
        seen: set = set()
        for vid in self._vector_store_ids:
            if vid and vid not in seen:
                uniq.append(vid)
                seen.add(vid)
        if uniq:
            self._fs_tools = [{"type": "file_search", "vector_store_ids": uniq}]
    try:
        all_file_ids = list(self.cfg.attached_file_ids or [])
        input_file_ids, input_image_ids = self._build_input_attachments(client, self._input_file_ids())
        zip_supported = bool(self._diag_zip_path and self._is_supported_input_file(self._diag_zip_path))
        supports_input_file = bool(self.cfg.model_caps.get("supports_input_file", True))
        self.log.event(
            "io.reference",
            {
                "file_ids": all_file_ids,
                "input_file_ids": list(input_file_ids),
                "input_image_ids": list(input_image_ids),
                "vector_store_ids": list(self._vector_store_ids or []),
                "use_file_search": bool(self.cfg.use_file_search),
                "supports_file_search": bool(self.cfg.model_caps.get("supports_file_search", False)),
                "supports_vector_store": bool(self.cfg.model_caps.get("supports_vector_store", False)),
                "supports_input_file": supports_input_file,
                "diagnostics_zip": self._diag_zip_path or None,
                "diagnostics_zip_supported_input": zip_supported,
                "file_search": bool(self._fs_tools),
            },
        )
    except (OSError, ValueError, TypeError) as exc:
        self.log.exception("attachments.reference", exc)

    self._runtime_diag_file_ids = diag_file_ids


def _approved_project_items(self: RunContext, root: str):
    root_path = Path(root).resolve()
    run_root = Path(self.log.paths.run_dir).resolve()
    rows = []
    legacy_reapproved: list[dict[str, Any]] = []
    legacy_scan: dict[str, Any] | None = None
    is_junction = getattr(os.path, "isjunction", lambda value: False)

    def legacy_policy_item(relative: str):
        nonlocal legacy_scan
        if legacy_scan is None:
            from ..filescan import scan_tree

            policy = self.settings.security
            scanned = scan_tree(
                str(root_path),
                root_path.name,
                [
                    ".git", "venv", ".venv", "LOG", "cache",
                    "__pycache__", "node_modules", ".pytest_cache",
                    ".ruff_cache",
                ],
                policy.deny_extensions_in,
                policy.allow_extensions_in,
                policy.deny_globs_in,
                policy.allow_globs_in,
                allow_sensitive=policy.allow_upload_sensitive,
            )
            legacy_scan = {item.rel_path: item for item in scanned}
        return legacy_scan.get(relative)

    for artifact in self.log.bundle.artifacts():
        if artifact.get("role") != "in_project_file":
            continue
        metadata = artifact.get("metadata") or {}
        decision = str(metadata.get("policy_decision") or "")
        rel = str(
            metadata.get("relative_path")
            or artifact.get("reconstruction_role")
            or ""
        )
        expected = str(metadata.get("sha256") or artifact.get("sha256") or "")
        bundle_rel = str(artifact.get("path_in_bundle") or "")
        if not rel or not expected or not bundle_rel:
            raise ContractError("SOURCE_PACK obsahuje neúplný schválený záznam.")

        if decision and decision not in {"approved", "approved_asset"}:
            continue
        if decision not in {"approved", "approved_asset"}:
            current_policy = legacy_policy_item(rel)
            if current_policy is None:
                raise ContractError(
                    f"Starý SourcePack nemá doložitelné schválení zdroje: {rel}"
                )
            binary_asset = project_binary_asset_candidate(current_policy)
            if not current_policy.uploadable and not binary_asset:
                raise ContractError(
                    f"Starý SourcePack nelze znovu použít: {rel} "
                    f"je nyní odmítnut politikou ({current_policy.reason})."
                )
            current_sha = sha256_file(str(Path(current_policy.abs_path)))
            if (
                current_sha != expected
                or current_policy.size != int(metadata.get("size") or current_policy.size)
            ):
                raise ContractError(
                    f"Starý SourcePack nelze znovu schválit se stejným obsahem: {rel}"
                )
            if binary_asset:
                validate_project_binary_asset(Path(current_policy.abs_path).read_bytes())
            legacy_reapproved.append(
                {
                    "path": rel,
                    "size": current_policy.size,
                    "sha256": expected,
                    "reason": current_policy.reason,
                    "sensitive": bool(current_policy.sensitive),
                }
            )

        current = Path(safe_join_under_root(str(root_path), rel))
        if current.is_symlink() or is_junction(str(current)) or not current.is_file():
            raise ContractError(f"Schválený zdroj změnil typ nebo chybí: {rel}")
        if sha256_file(str(current)) != expected:
            raise ContractError(f"IN se od schváleného SourcePacku změnil: {rel}")
        frozen = (run_root / bundle_rel).resolve()
        try:
            frozen.relative_to(run_root)
        except ValueError as exc:
            raise ContractError(f"SourcePack artifact uniká z RunBundle: {rel}") from exc
        if not frozen.is_file() or sha256_file(str(frozen)) != expected:
            raise ContractError(f"Immutable SourcePack artifact je poškozen: {rel}")
        rows.append(SimpleNamespace(
            rel_path=rel,
            abs_path=str(current),
            frozen_path=str(frozen),
            size=current.stat().st_size,
            sha256=expected,
            uploadable=True,
            reason=(
                "approved_source_pack"
                if decision in {"approved", "approved_asset"}
                else "legacy_policy_reapproved"
            ),
            sensitive=False,
        ))

    if legacy_reapproved:
        self.log.save_json(
            "manifests",
            "legacy_source_reapproval_v1",
            {
                "version": 1,
                "policy": "current_security_policy",
                "approved": sorted(
                    legacy_reapproved,
                    key=lambda row: str(row["path"]),
                ),
                "content_included": False,
            },
        )
    rows.sort(key=lambda item: item.rel_path)
    return rows

def _zip_in_dir(self: RunContext, root: str) -> str:
    root = os.path.abspath(root)
    ensure_dir(self.log.paths.files_dir)
    zip_path = os.path.join(self.log.paths.files_dir, f"in_dir_{ts_code()}.txt")
    items = _approved_project_items(self, root)
    with open(zip_path, "w", encoding="utf-8", newline="\n") as bundle:
        for item in items:
            self._check_stop()
            content = Path(item.frozen_path).read_bytes()
            try:
                decoded = content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            bundle.write(
                json.dumps(
                    {"path": item.rel_path, "sha256": item.sha256, "content": decoded},
                    ensure_ascii=False,
                )
                + "\n"
            )
            if bundle.tell() > 40 * 1024 * 1024:
                raise ValueError("Textový balíček IN překračuje limit 40 MiB.")
    return zip_path

def _prepare_in_dir_upload(self: RunContext, client: OpenAIClient) -> dict[str, Any] | None:
    in_dir = (self.cfg.in_dir or "").strip()
    if not in_dir or not os.path.isdir(in_dir):
        return None
    self._set(4, 0, "Kontroluji a nahrávám vstupní data…", stage="Vstupní data")
    zip_path = self._zip_in_dir(in_dir)
    up = client.upload_file(zip_path, purpose='user_data')
    file_id = up["id"]
    self._remember_file_name(file_id, os.path.basename(zip_path))
    info: dict[str, Any] = {"zip_path": zip_path, "file_id": file_id, "vector_store_id": None}
    try:
        self.log.event("upload.in_dir", {"zip": zip_path, "file_id": file_id, "bytes": os.path.getsize(zip_path)})
    except Exception as evidence_error:
        logging.getLogger(__name__).warning(
            "Zápis pomocné evidence selhal: %s", evidence_error
        )

    if (not self.cfg.send_as_c or self.cfg.mode == "GENERATE") and self._preparation_cap("supports_vector_store"):
        try:
            self._set(6, 0, "Indexuji vstupní data pro file_search…", stage="Indexace")
            vs = client.create_vector_store(f"IN_{ts_code()}")
            vs_id = vs.get("id")
            if vs_id:
                vs_file = client.add_file_to_vector_store(vs_id, file_id)
                vs_file_id = str(vs_file.get("id") or "")
                if vs_file_id:
                    self._wait_vector_store_files(client, vs_id, [vs_file_id])
                info["vector_store_id"] = vs_id
                try:
                    self.log.event("vector_store.in_dir", {"vector_store_id": vs_id, "file_id": file_id})
                except Exception as evidence_error:
                    logging.getLogger(__name__).warning(
                        "Zápis pomocné evidence selhal: %s", evidence_error
                    )
        except SubmissionOutcomeUnknown:
            raise
        except (OpenAIError, OSError, ValueError, RuntimeError) as e:
            try:
                self.log.exception("vector_store.in_dir", e)
                self.log.update_state({"in_context_delivery": {
                    "vector_store": "failed", "direct_input_file": True, "file_id": file_id, "error": str(e)
                }})
            except Exception as evidence_error:
                logging.getLogger(__name__).warning("Zápis evidence indexace selhal: %s", type(evidence_error).__name__)
            self.progress_event.emit(ProgressEvent(
                "Indexace", detail="Indexace vstupu selhala; pokračuji přes kanonickou přímou textovou přílohu."
            ))
    return info


def _files_with_in_dir(self: RunContext, file_ids: list[str]) -> list[str]:
    ids = list(file_ids or [])
    fid = self._in_dir_info.get("file_id") if self._in_dir_info else None
    if fid and fid not in ids:
        ids.append(fid)
    return ids


def _is_supported_input_file(self: RunContext, path: str) -> bool:
    ext = os.path.splitext(path or "")[1].lower()
    return bool(ext and ext in SUPPORTED_INPUT_FILE_EXTS)


def _input_files_with_in_dir(self: RunContext, file_ids: list[str]) -> list[str]:
    ids = list(file_ids or [])
    fid = self._in_dir_info.get("file_id") if self._in_dir_info else None
    zpath = str(self._in_dir_info.get("zip_path") or "") if self._in_dir_info else ""
    if fid and fid not in ids:
        if zpath and self._is_supported_input_file(zpath):
            ids.append(fid)
        else:
            self._log_debug("IN: ZIP není podporovaný input_file; přeskočeno v input.")
    return ids


def _build_input_attachments(self: RunContext, client: OpenAIClient, base_ids: list[str]) -> tuple[list[str], list[str]]:
    candidate_ids = self._input_files_with_in_dir(base_ids)
    file_ids: list[str] = []
    image_ids: list[str] = []
    seen: set = set()
    for fid in candidate_ids:
        fid_s = str(fid or "").strip()
        if not fid_s or fid_s in seen:
            continue
        seen.add(fid_s)
        kind = self._classify_input_kind(client, fid_s)
        if kind == "input_image":
            image_ids.append(fid_s)
        elif kind == "input_file":
            file_ids.append(fid_s)
        else:
            raise ContractError(f"Nepodporovaný formát přímé přílohy: {fid_s}")
    if file_ids or image_ids:
        metadata = [client.retrieve_file(fid) for fid in file_ids + image_ids]
        validate_input_file_sizes(metadata)
    return file_ids, image_ids


def _io_reference_note(self: RunContext, file_ids: list[str]) -> str:
    ids = [fid for fid in (file_ids or []) if fid]
    vs_ids = [vid for vid in (self._vector_store_ids or []) if vid]
    if not ids and not vs_ids:
        return ""
    parts: list[str] = ["DATA REFERENCE:"]
    if ids:
        parts.append(f"Files API file_id: {', '.join(ids)}")
        parts.append("Pouzij soubory v inputu automaticky podle typu a podporovaneho formatu: dokumenty jako input_file, obrazky (napr. PNG/JPG/WEBP/GIF) jako input_image.")
    if vs_ids:
        parts.append(f"Vector store id: {', '.join(vs_ids)}")
        parts.append("Pokud model podporuje file_search, pouzij file_search nad uvedenymi vector store.")
    return "\n".join(parts)


def _append_io_reference(self: RunContext, text: str, file_ids: list[str]) -> str:
    note = self._io_reference_note(file_ids)
    if not note:
        return text
    if note in text:
        return text
    return f"{text}\n\n{note}"


def _append_io_reference_instructions(self: RunContext, instructions: str, file_ids: list[str]) -> str:
    note = self._io_reference_note(file_ids)
    if not note:
        return instructions
    if note in instructions:
        return instructions
    return f"{instructions}\n\n{note}"


def _attach_diagnostics_vector_store(self: RunContext, client: OpenAIClient, diag_file_ids: list[str]) -> None:
    if not diag_file_ids:
        return
    supports_vs = self._preparation_cap("supports_vector_store")
    supports_fs = self._preparation_cap("supports_file_search")
    if not (supports_vs and supports_fs):
        raise RuntimeError("Diagnostics IN vyžaduje model s podporou vector store + file_search.")
    self._log_debug("Diagnostics IN: create vector store...")
    vs = client.create_vector_store(f"DIAG_{ts_code()}")
    vs_id = str(vs.get("id") or "")
    if not vs_id:
        raise RuntimeError("Diagnostics IN: nepodařilo se vytvořit vector store.")
    self._log_debug("Diagnostics IN: add JSON file_id to vector store...")
    vs_file_ids: list[str] = []
    for fid in diag_file_ids:
        if not fid:
            continue
        vs_file = client.add_file_to_vector_store(vs_id, fid)
        vs_file_id = str(vs_file.get("id") or "")
        if vs_file_id:
            vs_file_ids.append(vs_file_id)
    if vs_file_ids:
        self._log_debug("Diagnostics IN: wait for vector store indexing...")
        self._wait_vector_store_files(client, vs_id, vs_file_ids)
    self._diag_vector_store_ids.append(vs_id)
    self._vector_store_ids.append(vs_id)
    self._log_debug(f"Diagnostics: vector store {vs_id} attached.")


def _wait_vector_store_files(self: RunContext, client: OpenAIClient, vs_id: str, vs_file_ids: list[str], timeout_s: int = 180) -> None:
    context = VectorStorePollingContext(
        retrieve=lambda vector_store_id, file_id: client.retrieve_vector_store_file(vector_store_id, file_id),
        check_stop=self._check_stop,
        progress_emit=self.progress_event.emit,
        evidence_emit=lambda event, payload: self.log.event(event, payload),
        timeout_s=timeout_s,
    )
    wait_vector_store_files(context, vs_id, vs_file_ids)


def _in_dir_fallback_note(self: RunContext) -> str:
    if not self._in_dir_info or not self._in_dir_info.get("file_id"):
        return ""
    supports_fs = self._preparation_cap("supports_file_search")
    supports_vs = self._preparation_cap("supports_vector_store")
    if supports_fs or supports_vs:
        return ""
    return f"IN adresář je přiložen jako textový balíček (file_id={self._in_dir_info['file_id']}). Každý řádek JSON obsahuje cestu a obsah souboru."

def prepare_modify_inputs(self: RunContext, client: OpenAIClient, diag_file_ids: list[str]):
    """Připraví MODIFY výhradně ze schváleného SourcePacku a explicitních příloh."""
    self._set(8, 0, "Ověřuji zmrazený vstupní projekt IN…", stage="Vstupní data")
    root = self.cfg.in_dir
    items = _approved_project_items(self, root)
    up_items = list(items)

    tools = list(self._fs_tools or []) or None
    supports_fs = bool(
        tools and self._preparation_cap("supports_file_search")
    )
    vs_id = (
        str(self._in_dir_info.get("vector_store_id") or "")
        if self._in_dir_info
        else ""
    ) or None

    b_text = self.cfg.prompt or ""
    if self.cfg.recovery_instruction:
        b_text += self._recovery_suffix()

    explicit_ids = self._input_file_ids() + list(diag_file_ids or [])
    b_input_files, b_input_images = self._build_input_attachments(
        client, explicit_ids
    )
    references = self._files_with_in_dir(
        list(self.cfg.attached_file_ids or [])
        + list(diag_file_ids or [])
    )
    b_text = self._with_diag_text(self._append_io_reference(b_text, references))

    self.log.save_json(
        "manifests",
        "response_modify_context",
        {
            "approved_paths": [
                {"path": item.rel_path, "sha256": item.sha256, "size": item.size}
                for item in items
            ],
            "tools": tools,
            "supports_fs": supports_fs,
            "vs_id": vs_id,
            "input_files": b_input_files,
            "input_images": b_input_images,
        },
    )
    return (
        root,
        items,
        up_items,
        tools,
        supports_fs,
        vs_id,
        b_text,
        b_input_files,
        b_input_images,
    )

