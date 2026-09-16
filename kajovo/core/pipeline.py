from __future__ import annotations
import copy

import base64
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QThread, QLockFile
from pathlib import Path
from types import SimpleNamespace
from .response_journal import ResponseJournal, ResponsePending, SubmissionUnknown, ResponseCancelled
from .progress import ProgressEvent

from .request_rules import uses_reasoning_defaults, validate_run_options
from .structured_output import prepare_payload, validate_output, user_text, text_format, OutputContractError
from .compat import validate_input_file_sizes
from .generate_batch import build_manifest as build_batch_manifest
from .generate_batch import encode_requests
from .delivery_preparation import prepare_delivery, validate_preparation_snapshot, validate_modify_sources
from .requirements import apply_quality, stage_instructions
from .contracts import validate_chunk_metadata
from .contracts import ContractError, extract_text_from_response, parse_json_strict, validate_paths, file_response_format
from .filescan import build_manifest, scan_tree
from .openai_client import OpenAIClient
from .batch_submit import submit_verified_batch
from .retry import CircuitBreaker, with_retry
from .utils import ensure_dir, is_versing_snapshot_dir, sha256_file, ts_code, safe_join_under_root, atomic_write_text

from .compat import SUPPORTED_INPUT_FILE_EXTS, SUPPORTED_INPUT_IMAGE_EXTS


def split_text(text: str, max_chars: int) -> List[str]:
    if not text:
        return [""]
    if max_chars <= 0:
        return [text]
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        out.append(text[i : i + max_chars])
        i += max_chars
    return out


@dataclass
class UiRunConfig:
    project: str
    prompt: str
    mode: str  # GENERATE|MODIFY|QA|QFILE
    send_as_c: bool
    model: str
    model_a1: str
    model_a2: str
    model_a3: str
    response_id: str
    attached_file_ids: List[str]
    input_file_ids: List[str]
    attached_vector_store_ids: List[str]
    in_dir: str
    out_dir: str
    in_equals_out: bool
    versing: bool
    temperature: float
    use_file_search: bool

    diag_windows_in: bool
    diag_windows_out: bool
    diag_ssh_in: bool
    diag_ssh_out: bool
    ssh_user: str
    ssh_host: str
    ssh_key: str
    ssh_password: str
    skip_paths: List[str]
    skip_exts: List[str]

    # Snímek schopností vybraného modelu z lokální validace a pevné matice.
    model_caps: Dict[str, Any]
    # Podklady ReRun: seznam souborů a ID předchozí odpovědi.
    resume_files: List[Dict[str, Any]] = None  # type: ignore
    resume_prev_id: Optional[str] = None
    ssh_pin: str = ""
    ssh_pin_required: bool = False
    caps_by_model: Optional[Dict[str, Any]] = None
    # Aktuální katalog modelů z API; při jeho předání se vyžaduje povolení v pevné matici.
    available_models: Optional[List[str]] = None
    maximum_quality: bool = False
    preparation_snapshot: Optional[Dict[str, Any]] = None
    completed_hashes: Optional[Dict[str, str]] = None
    # Explicitní pokyn opravné větve. Používá se výhradně v nově
    # prováděné části za ověřeným checkpointem.
    recovery_instruction: str = ""
    source_checkpoint_id: str = ""


class RunWorker(QThread):
    progress = Signal(int)
    progress_event = Signal(object)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    failure_detail = Signal(object)

    def __init__(
        self,
        cfg: UiRunConfig,
        settings,
        api_key: str,
        run_logger,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        self.cfg.maximum_quality = getattr(self.cfg, "maximum_quality", False)
        self.cfg.preparation_snapshot = getattr(self.cfg, "preparation_snapshot", None)
        self.settings = copy.deepcopy(settings)
        self.api_key = api_key
        self.log = run_logger
        self.breaker = CircuitBreaker(settings.retry.circuit_breaker_failures, settings.retry.circuit_breaker_cooldown_s)
        self._stop = False
        self._cancel_response = False
        self._response_journal = None
        self._response_file_ids = {}
        self._last_prev_id_error: Optional[str] = None
        self._final_response_id: Optional[str] = None
        self._in_dir_info: Optional[Dict[str, Any]] = None
        self._fs_tools: Optional[List[Dict[str, Any]]] = None
        self._vector_store_ids: List[str] = []
        self._diag_vector_store_ids: List[str] = []
        self._diag_text: str = ""
        self._diag_zip_path: str = ""
        self._input_kind_cache: Dict[str, str] = {}
        self._file_name_cache: Dict[str, str] = {}

    def _ts(self) -> str:
        return time.strftime("%Y%m%d %H%M%S")

    def _recovery_suffix(self) -> str:
        instruction = str(getattr(self.cfg, "recovery_instruction", "") or "").strip()
        if not instruction:
            return ""
        return (
            "\n\n[NOVÁ VĚTEV – explicitní pokyn platí pouze pro nově prováděnou část]\n"
            + instruction
        )

    def _log_debug(self, msg: str) -> None:
        line = f"{self._ts()} | {msg}"
        try:
            self.logline.emit(line)
            self.log.event("debug", {"ts": self._ts(), "msg": msg})
        except Exception:
            pass

    def _log_api_action(self, stage: str, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        ts = self._ts()
        parts = [f"{stage}: {action}"]
        if details:
            for key, value in details.items():
                if value is None:
                    continue
                parts.append(f"{key}={value}")
        line = f"{ts} | " + " | ".join(parts)
        try:
            self.logline.emit(line)
            event = {"ts": ts, "stage": stage, "action": action}
            if details:
                event.update({k: v for k, v in details.items() if v is not None})
            self.log.event("api.trace", event)
            if event.get("response_id"):
                try:
                    self._final_response_id = str(event.get("response_id") or "")
                except Exception:
                    pass
                patch = {"last_response_id": str(event.get("response_id")), "last_response_stage": stage}
                contract = event.get("contract") or details.get("contract") if details else None
                if contract in ("A2_STRUCTURE", "B2_STRUCTURE"):
                    patch["last_structure_response_id"] = str(event.get("response_id"))
                if contract in ("A1_PLAN", "A2_STRUCTURE", "B1_PLAN", "B2_STRUCTURE"):
                    patch["last_plan_response_id"] = str(event.get("response_id"))
                try:
                    self.log.update_state(patch)
                except Exception:
                    pass
        except Exception:
            pass

    def _attachments_snapshot(
        self,
        stage: str,
        ref_file_ids: List[str],
        input_file_ids: List[str],
        input_image_ids: List[str],
        vector_store_ids: List[str],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
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

    def _input_file_ids(self) -> List[str]:
        try:
            if hasattr(self.cfg, "input_file_ids"):
                return list(self.cfg.input_file_ids or [])
        except Exception:
            pass
        return list(self.cfg.attached_file_ids or [])

    def _generate_model(self, step: str) -> str:
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

    def _model_caps(self, model):
        return self.cfg.model_caps if model == self.cfg.model else (self.cfg.caps_by_model or {}).get(model, {})

    def _preparation_cap(self, name):
        if self.cfg.mode == "GENERATE":
            return all(self._model_caps(self._generate_model(step)).get(name, False) for step in ("A1", "A2"))
        return bool(self.cfg.model_caps.get(name, False))

    def _remember_file_name(self, file_id: str, name: str) -> None:
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

    def _classify_input_kind(self, client: OpenAIClient, file_id: str) -> str:
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
            meta = with_retry(lambda f=fid: client.retrieve_file(f), self.settings.retry, self.breaker)
            filename = str(meta.get("filename") or "").strip()
            self._remember_file_name(fid, filename)
        except Exception as e:
            raise ContractError(f"Nelze ověřit připojený soubor {fid}.") from e
        kind = self._input_kind_cache.get(fid, "unsupported")
        if kind == "unsupported":
            shown = filename or "(unknown filename)"
            self._log_debug(f"Unsupported input attachment type for {fid} ({shown}); keeping only reference.")
        return kind

    def _log_request_attachments(
        self,
        stage: str,
        ref_file_ids: List[str],
        input_file_ids: List[str],
        input_image_ids: List[str],
        vector_store_ids: List[str],
        tools: Optional[List[Dict[str, Any]]],
    ) -> None:
        snapshot = self._attachments_snapshot(stage, ref_file_ids, input_file_ids, input_image_ids, vector_store_ids, tools)
        try:
            self.log.event("request.attachments", snapshot)
        except Exception:
            pass
        if snapshot.get("file_ids") or snapshot.get("input_file_ids") or snapshot.get("input_image_ids") or snapshot.get("vector_store_ids") or snapshot.get("tool_types"):
            self._log_debug(
                f"{stage}: attachments files={len(snapshot.get('file_ids') or [])} input_files={len(snapshot.get('input_file_ids') or [])} "
                f"input_images={len(snapshot.get('input_image_ids') or [])} vector_stores={len(snapshot.get('vector_store_ids') or [])} "
                f"tools={','.join(snapshot.get('tool_types') or []) or 'none'}"
            )

    def request_stop(self):
        self._stop = True

    def request_cancel_response(self):
        self._cancel_response = True

    def _check_stop(self):
        if self._stop or self._cancel_response:
            raise RuntimeError("STOP_REQUESTED")

    def _set(self, p: int, sp: int, msg: str, *, stage=None, source="local", next_step=""):
        if stage:
            self._progress_stage = stage
        self.progress.emit(p)
        self.subprogress.emit(sp)
        self.status.emit(msg)
        self.progress_event.emit(ProgressEvent(getattr(self, "_progress_stage", "Příprava"), detail=msg,
                                               source=source, next_step=next_step))
        self._log_debug(msg)
        try:
            self.log.event("ui.progress", {"p": p, "sp": sp, "msg": msg, "ts": self._ts()})
        except Exception:
            pass

    def run(self):
        lock = QLockFile(str(Path(self.log.paths.run_dir) / "execution.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            self.finished_err.emit("Tento běh již používá jiná instance aplikace.")
            return
        try:
            # Každý nový běh ukládá přesný rekonstruovatelný vstup ještě před
            # prvním síťovým požadavkem. RunLogger z něj vytvoří kanonický
            # input_ready checkpoint; u legacy záznamů se nic nedopočítává.
            self.log.update_state({"ui_state": self.cfg.__dict__})
            if self.cfg.mode in ("GENERATE", "MODIFY"):
                self._response_journal = ResponseJournal(self.log, self.settings.response_poll_timeout_s)
                saved_state = json.loads(Path(self.log.state_path).read_text(encoding="utf-8"))
                self._response_file_ids = saved_state.get("response_file_ids", {})
                self.log.update_state({"response_transport": "background"})

            if not self.api_key or not self.cfg.model or not self.cfg.prompt.strip():
                raise ValueError("Běh vyžaduje API klíč, model a neprázdné zadání.")
            validate_run_options(self.cfg, check_models=False)
            if self.cfg.mode == "MODIFY" and not (
                self.cfg.in_dir and os.path.isdir(self.cfg.in_dir)
            ) and not getattr(self, "resume_generate_batch", None):
                raise ValueError("MODIFY vyžaduje existující vstupní adresář IN.")
            if self.cfg.resume_files is not None:
                validate_paths(self.cfg.resume_files)
            if self.cfg.mode in ("GENERATE", "MODIFY"):
                self._verify_completed_files()
                if self.cfg.preparation_snapshot:
                    validate_preparation_snapshot(self.cfg.preparation_snapshot, self.cfg.mode, self.cfg.maximum_quality)
            self.log.update_state(
                {
                    "status": "running", "error": None, "failure_detail": None, "failed_at": None,
                    "started_at": time.time(),
                    "mode": self.cfg.mode,
                    "send_as_c": self.cfg.send_as_c,
                    "model": self.cfg.model,
                    "out_dir": self.cfg.out_dir,
                }
            )
            client = OpenAIClient(self.api_key, timeout_s=self.settings.response_timeout_s)
            client.configure_validation(self.settings)
            client.stopped = lambda: self._stop
            if getattr(self, "resume_generate_batch", None):
                result = self._submit_generate_batch(client, copy.deepcopy(self.resume_generate_batch))
                self.finished_ok.emit(result)
                return
            self._set(1, 0, "Lokálně ověřuji model, účet a parametry běhu…", stage="Lokální validace")
            client.prepare_run_validation(self.cfg)
            validate_run_options(self.cfg)

            if self.cfg.mode == "QFILE" and self.cfg.send_as_c:
                raise RuntimeError("QFILE nepodporuje SEND AS BATCH.")

            # GENERATE a MODIFY vyžadují návaznost; výslovné odmítnutí ji zablokuje.
            if self.cfg.mode == "MODIFY" and self.cfg.model_caps.get("supports_previous_response_id") is False:
                raise RuntimeError("Selected model explicitly rejects previous_response_id (required for cascades).")

            runtime_path = self.log.find_json("manifests", "response_runtime") if self._response_journal else None
            if runtime_path:
                runtime = json.loads(Path(runtime_path).read_text(encoding="utf-8"))
                for name, value in runtime["attributes"].items():
                    setattr(self, name, value)
                diag_file_ids = runtime["diag_file_ids"]
                self.cfg.preparation_snapshot = runtime["preparation_snapshot"]
                self.cfg.response_id = runtime["response_id"]
                self.cfg.resume_files = runtime["resume_files"]
                self.cfg.resume_prev_id = runtime["resume_prev_id"]
            else:
                self._prepare_response_runtime(client)
                diag_file_ids = self._runtime_diag_file_ids
                if self._response_journal:
                    self.log.save_json("manifests", "response_runtime", {
                        "attributes": {name: getattr(self, name) for name in (
                            "_diag_text", "_in_dir_info", "_vector_store_ids", "_diag_vector_store_ids",
                            "_fs_tools", "_diag_zip_path", "_input_kind_cache", "_file_name_cache")},
                        "diag_file_ids": diag_file_ids, "preparation_snapshot": self.cfg.preparation_snapshot,
                        "response_id": self.cfg.response_id, "resume_files": self.cfg.resume_files,
                        "resume_prev_id": self.cfg.resume_prev_id,
                    })

            # Zpracování dlouhého zadání.
            # GENERATE/MODIFY zavádí zadání přes A0 s previous_response_id.
            # QA odesílá zadání v textových částech zprávy.
            if self.cfg.preparation_snapshot and self.cfg.mode in ("GENERATE", "MODIFY"):
                checkpoint = validate_preparation_snapshot(self.cfg.preparation_snapshot, self.cfg.mode, self.cfg.maximum_quality)
                base_prev_id = checkpoint["response_id"]
            elif self.cfg.mode in ("GENERATE", "MODIFY"):
                base_prev_id = self._ingest_prompt_if_needed(client, prev_id=self.cfg.response_id or None)
            else:
                base_prev_id = self.cfg.response_id or None

            if self.cfg.mode in ("GENERATE", "MODIFY", "QA", "QFILE"):
                if self.cfg.mode == "GENERATE":
                    result = self._run_a_generate(client, diag_file_ids, base_prev_id)
                elif self.cfg.mode == "MODIFY":
                    result = self._run_b_modify(client, diag_file_ids, base_prev_id)
                elif self.cfg.mode == "QA":
                    result = self._run_qa(client, diag_file_ids, base_prev_id)
                elif self.cfg.mode == "QFILE":
                    result = self._run_qfile(client, diag_file_ids, base_prev_id)
                else:
                    raise RuntimeError(f"Unknown mode: {self.cfg.mode}")
            if self._final_response_id:
                result["last_response_id"] = self._final_response_id
                if not result.get("response_id"):
                    result["response_id"] = self._final_response_id

            final_status = "batch_pending" if result.get("batch_id") else str(result.get("status") or "completed")
            if final_status not in ("completed", "partial", "batch_pending", "dry_run", "files_complete_unverified"):
                raise ContractError(f"Neplatný terminální stav běhu: {final_status}")
            if final_status in ("completed", "partial", "dry_run", "files_complete_unverified"):
                self.log.update_state({"status": final_status, "completed_at": time.time()})
            else:
                self.log.clear_state_keys("completed_at")
                self.log.update_state({"status": final_status})
            self.progress_event.emit(ProgressEvent("RUN", final_status))
            self.finished_ok.emit(result)
        except BaseException as e:
            measurement = getattr(e, "context_report", None)
            if measurement:
                from .cost_context_report import CostContextReport
                CostContextReport(self.log.paths.run_dir).record(
                    {"model": measurement["model"]}, custom_id=measurement["request_hash"],
                    measurement=measurement, status="blocked")
            msg = str(e)
            if self._last_prev_id_error:
                msg = self._last_prev_id_error
            if isinstance(e, (ResponsePending, SubmissionUnknown)):
                state = "response_pending" if isinstance(e, ResponsePending) else "submission_unknown"
                self.log.update_state({"status": state, "error": str(e)})
                self.progress_event.emit(ProgressEvent("RUN", state, detail=str(e)))
                self.finished_err.emit(str(e))
            elif isinstance(e, ResponseCancelled):
                self.log.update_state({"status": "cancelled", "error": str(e)})
                self.progress_event.emit(ProgressEvent("RUN", "cancelled"))
                self.finished_err.emit(str(e))
            elif str(e) == "STOP_REQUESTED":
                self.log.update_state({"status": "stopped", "stopped_at": time.time()})
                self.progress_event.emit(ProgressEvent("RUN", "cancelled"))
                self.finished_err.emit("STOPPED")
            else:
                try:
                    self.log.exception("run", e)
                except Exception:
                    pass
                self.log.update_state({"status": "failed", "failed_at": time.time(), "error": str(e)})
                from .user_errors import describe_error
                self.failure_detail.emit(describe_error(e))
                self.progress_event.emit(ProgressEvent("RUN", "failed"))
                self.finished_err.emit(msg)
        finally:
            lock.unlock()

    def _prepare_response_runtime(self, client):
        if self._input_file_ids():
            self._set(2, 0, "Ověřuji vstupní přílohy…", stage="Přílohy")
        input_files, input_images = self._build_input_attachments(client, self._input_file_ids())
        if input_files or input_images:
            for model in self.cfg.caps_by_model:
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
            uniq: List[str] = []
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
        except Exception:
            pass

        self._runtime_diag_file_ids = diag_file_ids

    def _verify_completed_files(self):
        """ReRun přeskočí pouze doložený soubor, který uživatel mezitím nezměnil."""
        hashes = getattr(self.cfg, "completed_hashes", None) or {}
        entries = []
        for path in self.cfg.skip_paths or []:
            target = safe_join_under_root(self.cfg.out_dir, path)
            if not hashes.get(path) or not os.path.isfile(target) or sha256_file(target) != hashes[path]:
                raise ContractError(f"ReRun: dokončený soubor nemá platný důkaz zápisu: {path}")
            entries.append({"path": path, "sha256": hashes[path], "dst": target})
        if entries:
            self.log.save_json("manifests", "out_completed_evidence", {
                "out_dir": self.cfg.out_dir, "saved": entries,
            })

    # Sestavení požadavků.
    def _input_parts(self, text: str, file_ids: List[str], image_file_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Sestaví vstup Responses API z textu a volitelných souborů či obrázků."""
        chunks = split_text(text, max_chars=20_000)
        if not chunks:
            chunks = [""]
        parts: List[Dict[str, Any]] = []
        image_ids = [fid for fid in (image_file_ids or []) if fid]
        for i, ch in enumerate(chunks):
            content: List[Dict[str, Any]] = [{"type": "input_text", "text": ch}]
            if i == 0 and file_ids:
                for fid in file_ids:
                    content.append({"type": "input_file", "file_id": fid})
            if i == 0 and image_ids:
                for fid in image_ids:
                    content.append({"type": "input_image", "file_id": fid})
            parts.append({"type": "message", "role": "user", "content": content})
        return parts

    def _payload_base(
        self,
        model: str,
        instructions: str,
        input_parts: List[Dict[str, Any]],
        prev_id: Optional[str],
        supports_temperature: Optional[bool] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_parts,
        }
        can_send_temperature = self.cfg.model_caps.get("supports_temperature", True) if supports_temperature is None else bool(supports_temperature)
        if can_send_temperature and not uses_reasoning_defaults(model):
            payload["temperature"] = float(self.cfg.temperature)
        if prev_id:
            payload["previous_response_id"] = prev_id
        payload["text"] = text_format()
        if self.cfg.mode in ("GENERATE", "MODIFY"):
            apply_quality(payload, self.cfg.maximum_quality)
        return payload

    # Diagnostika.
    def _build_diag_text(self, files: List[str]) -> str:
        allowed_exts = {
            ".txt", ".log", ".json", ".xml", ".yaml", ".yml", ".md", ".csv",
            ".ini", ".cfg", ".conf", ".ps1", ".bat", ".cmd", ".sh"
        }
        max_total = 120_000
        max_per_file = 20_000
        parts: List[str] = []
        total = 0
        for fp in files:
            if total >= max_total:
                break
            ext = os.path.splitext(fp)[1].lower()
            if ext and ext not in allowed_exts:
                continue
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(max_per_file)
            except Exception:
                continue
            if not content.strip():
                continue
            header = f"\n# {os.path.basename(fp)}\n"
            if total + len(header) >= max_total:
                break
            parts.append(header)
            total += len(header)
            if total + len(content) > max_total:
                content = content[: max_total - total]
            parts.append(content)
            total += len(content)
        return "".join(parts).strip()

    def _write_diagnostics_json(self, root: str, files: List[str]) -> Optional[str]:
        if not root or not os.path.isdir(root):
            return None
        ensure_dir(self.log.paths.files_dir)
        json_path = os.path.join(self.log.paths.files_dir, f"diagnostics_{ts_code()}.json")
        try:
            text_exts = {
                ".txt", ".log", ".json", ".xml", ".yaml", ".yml", ".md", ".csv",
                ".ini", ".cfg", ".conf", ".ps1", ".bat", ".cmd", ".sh", ".reg"
            }
            total_size = 0
            payload: Dict[str, Any] = {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source_root": root,
                "file_count": 0,
                "total_size_bytes": 0,
                "files": [],
            }
            for fp in files:
                rel = os.path.relpath(fp, root)
                ext = os.path.splitext(fp)[1].lower()
                try:
                    size = os.path.getsize(fp)
                except Exception:
                    size = None
                try:
                    if ext in text_exts:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        payload["files"].append({"path": rel, "encoding": "utf-8", "content": content, "bytes": size})
                    else:
                        with open(fp, "rb") as f:
                            data = f.read()
                        b64 = base64.b64encode(data).decode("ascii")
                        payload["files"].append({"path": rel, "encoding": "base64", "content": b64, "bytes": size})
                    payload["file_count"] += 1
                    if size:
                        total_size += int(size)
                except Exception:
                    continue
            payload["total_size_bytes"] = total_size
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return json_path
        except Exception:
            return None

    def _maybe_collect_diagnostics(self, client: OpenAIClient) -> Tuple[List[str], str]:
        diag_file_ids: List[str] = []
        diag_text = ""
        if not (self.cfg.diag_windows_in or self.cfg.diag_ssh_in):
            return diag_file_ids, diag_text

        self._set(2, 0, "Sbírám diagnostická data…", stage="Diagnostika")
        diag_root = os.path.join(self.log.paths.manifests_dir, "diagnostics")
        ensure_dir(diag_root)
        diag_files: List[str] = []

        if self.cfg.diag_windows_in:
            from .diagnostics.windows import collect_windows_diagnostics
            try:
                self._log_debug("Diagnostics IN: Windows collect...")
                folder, files = collect_windows_diagnostics(diag_root, on_line=self._log_debug)
                diag_files.extend(files)
                try:
                    self.log.event("diagnostics.windows.collected", {"folder": folder, "count": len(files)})
                except Exception:
                    pass
            except Exception as e:
                try:
                    self.log.exception("diagnostics.windows.failed", e)
                except Exception:
                    pass
                raise RuntimeError(f"Diagnostics Windows failed: {e}") from e

        if self.cfg.diag_ssh_in:
            from .diagnostics.ssh import collect_ssh_diagnostics
            if not (self.cfg.ssh_host and self.cfg.ssh_user):
                raise RuntimeError("Diagnostics SSH failed: missing SSH host/user.")
            try:
                self._log_debug("Diagnostics IN: SSH collect...")
                folder, files = collect_ssh_diagnostics(
                    diag_root,
                    self.cfg.ssh_host,
                    self.cfg.ssh_user,
                    self.cfg.ssh_key,
                    self.cfg.ssh_password,
                    on_line=self._log_debug,
                    timeout_s=900,
                    pin=self.cfg.ssh_pin,
                    pin_required=self.cfg.ssh_pin_required,
                )
                diag_files.extend(files)
                try:
                    self.log.event("diagnostics.ssh.collected", {"folder": folder, "count": len(files)})
                except Exception:
                    pass
            except Exception as e:
                try:
                    self.log.exception("diagnostics.ssh.failed", e)
                except Exception:
                    pass
                raise RuntimeError(f"Diagnostics SSH failed: {e}") from e

        diag_text = self._build_diag_text(diag_files)

        self._log_debug("Diagnostics IN: write JSON bundle...")
        json_path = self._write_diagnostics_json(diag_root, diag_files)
        self._diag_zip_path = json_path or ""
        if json_path:
            try:
                self._log_debug("Diagnostics IN: upload JSON to Files API...")
                up = with_retry(lambda p=json_path: client.upload_file(p, purpose="user_data"), self.settings.retry, self.breaker)
                diag_file_ids.append(up["id"])
                self._remember_file_name(up["id"], os.path.basename(json_path))
                self.log.event("upload.diagnostics", {"local": json_path, "file_id": up["id"], "purpose": "user_data", "bytes": os.path.getsize(json_path)})
            except Exception as e:
                try:
                    self.log.exception("upload.diagnostics", e)
                    self.log.update_state({"diagnostics_delivery": {
                        "requested": True, "delivered": False, "error": str(e)
                    }})
                except Exception:
                    pass
                raise RuntimeError(f"Diagnostická data se nepodařilo doručit do Files API: {e}") from e
        if not diag_file_ids:
            self.log.update_state({"diagnostics_delivery": {
                "requested": True, "delivered": False, "error": "Nevznikl diagnostický JSON bundle."
            }})
            raise RuntimeError("Požadovaná diagnostika nebyla doručena; pracovní požadavek nebyl odeslán.")
        self.log.update_state({"diagnostics_delivery": {
            "requested": True, "delivered": True, "file_ids": list(diag_file_ids)
        }})
        return diag_file_ids, diag_text

    def _zip_in_dir(self, root: str) -> str:
        root = os.path.abspath(root)
        ensure_dir(self.log.paths.files_dir)
        zip_path = os.path.join(self.log.paths.files_dir, f"in_dir_{ts_code()}.txt")
        policy = self.settings.security
        items = scan_tree(root, os.path.basename(root), [".git", "venv", ".venv", "LOG", "cache", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"],
                          policy.deny_extensions_in, policy.allow_extensions_in,
                          policy.deny_globs_in, policy.allow_globs_in,
                          allow_sensitive=policy.allow_upload_sensitive)
        with open(zip_path, "w", encoding="utf-8", newline="\n") as bundle:
            for item in items:
                self._check_stop()
                if not item.uploadable:
                    continue
                import hashlib
                with open(item.abs_path, "rb") as source:
                    content = source.read(10 * 1024 * 1024 + 1)
                if hashlib.sha256(content).hexdigest() != item.sha256:
                    raise RuntimeError(f"Vstupní soubor se změnil během přípravy: {item.rel_path}")
                bundle.write(json.dumps({"path": item.rel_path, "content": content.decode("utf-8")}, ensure_ascii=False) + "\n")
                if bundle.tell() > 40 * 1024 * 1024:
                    raise ValueError("Textový balíček IN překračuje limit 40 MiB.")
        return zip_path

    def _prepare_in_dir_upload(self, client: OpenAIClient) -> Optional[Dict[str, Any]]:
        in_dir = (self.cfg.in_dir or "").strip()
        if not in_dir or not os.path.isdir(in_dir):
            return None
        self._set(4, 0, "Kontroluji a nahrávám vstupní data…", stage="Vstupní data")
        zip_path = self._zip_in_dir(in_dir)
        up = with_retry(lambda: client.upload_file(zip_path, purpose="user_data"), self.settings.retry, self.breaker)
        file_id = up["id"]
        self._remember_file_name(file_id, os.path.basename(zip_path))
        info: Dict[str, Any] = {"zip_path": zip_path, "file_id": file_id, "vector_store_id": None}
        try:
            self.log.event("upload.in_dir", {"zip": zip_path, "file_id": file_id, "bytes": os.path.getsize(zip_path)})
        except Exception:
            pass

        if (not self.cfg.send_as_c or self.cfg.mode == "GENERATE") and self._preparation_cap("supports_vector_store"):
            try:
                self._set(6, 0, "Indexuji vstupní data pro file_search…", stage="Indexace")
                vs = with_retry(lambda: client.create_vector_store(f"IN_{ts_code()}"), self.settings.retry, self.breaker)
                vs_id = vs.get("id")
                if vs_id:
                    vs_file = with_retry(lambda: client.add_file_to_vector_store(vs_id, file_id), self.settings.retry, self.breaker)
                    vs_file_id = str(vs_file.get("id") or "")
                    if vs_file_id:
                        self._wait_vector_store_files(client, vs_id, [vs_file_id])
                    info["vector_store_id"] = vs_id
                    try:
                        self.log.event("vector_store.in_dir", {"vector_store_id": vs_id, "file_id": file_id})
                    except Exception:
                        pass
            except Exception as e:
                try:
                    self.log.exception("vector_store.in_dir", e)
                    self.log.update_state({"in_context_delivery": {
                        "vector_store": "failed", "direct_input_file": True, "file_id": file_id, "error": str(e)
                    }})
                except Exception:
                    pass
                self.progress_event.emit(ProgressEvent(
                    "Indexace", detail="Indexace vstupu selhala; pokračuji přes kanonickou přímou textovou přílohu."
                ))
        return info

    def _files_with_in_dir(self, file_ids: List[str]) -> List[str]:
        ids = list(file_ids or [])
        fid = None
        try:
            fid = self._in_dir_info.get("file_id") if self._in_dir_info else None
        except Exception:
            fid = None
        if fid and fid not in ids:
            ids.append(fid)
        return ids

    def _is_supported_input_file(self, path: str) -> bool:
        ext = os.path.splitext(path or "")[1].lower()
        return bool(ext and ext in SUPPORTED_INPUT_FILE_EXTS)

    def _input_files_with_in_dir(self, file_ids: List[str]) -> List[str]:
        ids = list(file_ids or [])
        fid = None
        zpath = ""
        try:
            fid = self._in_dir_info.get("file_id") if self._in_dir_info else None
            zpath = str(self._in_dir_info.get("zip_path") or "") if self._in_dir_info else ""
        except Exception:
            fid = None
            zpath = ""
        if fid and fid not in ids:
            if zpath and self._is_supported_input_file(zpath):
                ids.append(fid)
            else:
                self._log_debug("IN: ZIP není podporovaný input_file; přeskočeno v input.")
        return ids

    def _build_input_attachments(self, client: OpenAIClient, base_ids: List[str]) -> Tuple[List[str], List[str]]:
        candidate_ids = self._input_files_with_in_dir(base_ids)
        file_ids: List[str] = []
        image_ids: List[str] = []
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
            metadata = [with_retry(lambda f=fid: client.retrieve_file(f), self.settings.retry, self.breaker)
                        for fid in file_ids + image_ids]
            validate_input_file_sizes(metadata)
        return file_ids, image_ids

    def _io_reference_note(self, file_ids: List[str]) -> str:
        ids = [fid for fid in (file_ids or []) if fid]
        vs_ids = [vid for vid in (self._vector_store_ids or []) if vid]
        if not ids and not vs_ids:
            return ""
        parts: List[str] = ["DATA REFERENCE:"]
        if ids:
            parts.append(f"Files API file_id: {', '.join(ids)}")
            parts.append("Pouzij soubory v inputu automaticky podle typu a podporovaneho formatu: dokumenty jako input_file, obrazky (napr. PNG/JPG/WEBP/GIF) jako input_image.")
        if vs_ids:
            parts.append(f"Vector store id: {', '.join(vs_ids)}")
            parts.append("Pokud model podporuje file_search, pouzij file_search nad uvedenymi vector store.")
        return "\n".join(parts)

    def _append_io_reference(self, text: str, file_ids: List[str]) -> str:
        note = self._io_reference_note(file_ids)
        if not note:
            return text
        if note in text:
            return text
        return f"{text}\n\n{note}"

    def _append_io_reference_instructions(self, instructions: str, file_ids: List[str]) -> str:
        note = self._io_reference_note(file_ids)
        if not note:
            return instructions
        if note in instructions:
            return instructions
        return f"{instructions}\n\n{note}"

    def _should_inline_diag_text(self) -> bool:
        return False

    def _attach_diagnostics_vector_store(self, client: OpenAIClient, diag_file_ids: List[str]) -> None:
        if not diag_file_ids:
            return
        supports_vs = self._preparation_cap("supports_vector_store")
        supports_fs = self._preparation_cap("supports_file_search")
        if not (supports_vs and supports_fs):
            raise RuntimeError("Diagnostics IN vyžaduje model s podporou vector store + file_search.")
        self._log_debug("Diagnostics IN: create vector store...")
        vs = with_retry(lambda: client.create_vector_store(f"DIAG_{ts_code()}"), self.settings.retry, self.breaker)
        vs_id = str(vs.get("id") or "")
        if not vs_id:
            raise RuntimeError("Diagnostics IN: nepodařilo se vytvořit vector store.")
        self._log_debug("Diagnostics IN: add JSON file_id to vector store...")
        vs_file_ids: List[str] = []
        for fid in diag_file_ids:
            if not fid:
                continue
            vs_file = with_retry(lambda v=vs_id, f=fid: client.add_file_to_vector_store(v, f), self.settings.retry, self.breaker)
            vs_file_id = str(vs_file.get("id") or "")
            if vs_file_id:
                vs_file_ids.append(vs_file_id)
        if vs_file_ids:
            self._log_debug("Diagnostics IN: wait for vector store indexing...")
            self._wait_vector_store_files(client, vs_id, vs_file_ids)
        self._diag_vector_store_ids.append(vs_id)
        self._vector_store_ids.append(vs_id)
        self._log_debug(f"Diagnostics: vector store {vs_id} attached.")

    def _with_diag_text(self, text: str) -> str:
        if not self._should_inline_diag_text():
            return text
        if "DIAGNOSTICS (PARSED):" in text:
            return text
        return f"{text}\n\nDIAGNOSTICS (PARSED):\n{self._diag_text}"

    def _wait_vector_store_files(self, client: OpenAIClient, vs_id: str, vs_file_ids: List[str], timeout_s: int = 180) -> None:
        if not vs_file_ids:
            return
        start = time.time()
        pending = set(vs_file_ids)
        while pending:
            self._check_stop()
            if time.time() - start > timeout_s:
                raise RuntimeError(f"Vector store index timeout ({vs_id}).")
            completed: List[str] = []
            for vs_file_id in list(pending):
                try:
                    info = with_retry(lambda v=vs_id, f=vs_file_id: client.retrieve_vector_store_file(v, f), self.settings.retry, self.breaker)
                except Exception:
                    continue
                status = str(info.get("status") or "")
                self.progress_event.emit(ProgressEvent("Indexace", detail=f"API ověřilo stav souboru: {status}",
                                                       source="files_api"))
                if status == "completed":
                    completed.append(vs_file_id)
                elif status == "failed":
                    last_error = info.get("last_error") or {}
                    msg = last_error.get("message") or "Vector store indexing failed."
                    raise RuntimeError(f"Vector store indexing failed ({vs_id}): {msg}")
            for done in completed:
                pending.discard(done)
            self.progress_event.emit(ProgressEvent("Indexace", completed=len(set(vs_file_ids)) - len(pending),
                                                   total=len(set(vs_file_ids)), unit="souborů", source="files_api"))
            if pending:
                time.sleep(2.0)

    def _in_dir_fallback_note(self) -> str:
        if not self._in_dir_info or not self._in_dir_info.get("file_id"):
            return ""
        supports_fs = self._preparation_cap("supports_file_search")
        supports_vs = self._preparation_cap("supports_vector_store")
        if supports_fs or supports_vs:
            return ""
        return f"IN adresář je přiložen jako textový balíček (file_id={self._in_dir_info['file_id']}). Každý řádek JSON obsahuje cestu a obsah souboru."

    # Zavedení dlouhého zadání.
    def _ingest_prompt_if_needed(self, client: OpenAIClient, prev_id: Optional[str]) -> Optional[str]:
        """Zachová přesný dlouhý vstup lokálně; příjem proběhne v pracovní A0R/B0R."""
        from .recoverable_artifacts import save_artifact
        prompt = self.cfg.prompt or ""
        if len(prompt) > 150_000:
            save_artifact(self.log.paths.run_dir, "source_prompt", {"text": prompt})
            self.progress_event.emit(ProgressEvent("A0", completed=1, total=1,
                unit="zadání", detail=f"Uloženo přesné zadání: {len(prompt):,} znaků; bez placených potvrzení částí."))
        return prev_id

    def _create_snapshot(self, root: str) -> str:
        root = os.path.abspath(root)
        root_name = os.path.basename(root)
        snap_name = f"{root_name}{time.strftime('%d%m%Y%H%M%S')}"
        snap_dir = os.path.join(root, snap_name)
        deny = {"venv", ".venv", "LOG", snap_name}

        def ignore(dirpath, names):
            ignored = set()
            for n in names:
                if n in deny:
                    ignored.add(n)
                elif is_versing_snapshot_dir(n, root_name):
                    ignored.add(n)
            return ignored

        shutil.copytree(root, snap_dir, ignore=ignore, symlinks=True)
        try:
            self.log.event("versing.snapshot.created", {"snap_dir": snap_dir})
        except Exception:
            pass
        return snap_dir

    def _save_out_files(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        out_dir = self.cfg.out_dir
        validate_paths(files)
        for row in files:
            safe_join_under_root(out_dir, row["path"])
            if not isinstance(row.get("content"), str):
                raise ContractError("Obsah výstupního souboru musí být text.")
        if self.cfg.mode == "MODIFY" and self.settings.dry_run_modify:
            self.log.save_json("manifests", "modify_dry_run", {"files": files})
            self.log.update_state({"dry_run": True, "written_files": []})
            self._finish_file_delivery(files, dry_run=True)
            return {"saved": [], "dry_run": True}
        ensure_dir(out_dir)

        if self.cfg.versing and files:
            self._set(80, 0, "Vytvářím snapshot před zápisem…", stage="VERSING")
            self._create_snapshot(out_dir)

        saved: List[Dict[str, Any]] = []
        self._progress_stage = "Ukládání"
        self.progress_event.emit(ProgressEvent("Ukládání", completed=0, total=len(files), unit="souborů"))
        for i, f in enumerate(files):
            self._check_stop()
            rel = f["path"]
            content = f["content"]
            dst = safe_join_under_root(out_dir, rel)
            ensure_dir(os.path.dirname(dst))
            if self.cfg.mode == "MODIFY" and hasattr(self, "_delivery_overwrite_hashes"):
                current_hash = sha256_file(dst) if os.path.isfile(dst) else None
                if current_hash != self._delivery_overwrite_hashes.get(rel):
                    raise ContractError(f"OUT se během generování změnil; soubor zachován: {rel}")
            before_size = os.path.getsize(dst) if os.path.exists(dst) else None
            before = sha256_file(dst) if os.path.exists(dst) else None
            if self.cfg.mode == "GENERATE" and before is not None:
                expected = (self.cfg.completed_hashes or {}).get(rel)
                incoming = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if before not in {expected, incoming}:
                    raise ContractError(f"Existující soubor nepatří ověřenému výstupu; zachován: {rel}")
            atomic_write_text(dst, content)
            after_size = os.path.getsize(dst)
            after = sha256_file(dst)
            if after != hashlib.sha256(content.encode("utf-8")).hexdigest():
                raise ContractError(f"Zápis neodpovídá ověřenému obsahu: {rel}")
            self.log.record_fs_change("write", src=rel, dst=dst, before=before, after=after, before_size=before_size, after_size=after_size)
            entry = {
                "path": rel, "dst": dst, "bytes": after_size, "sha256": after,
                "written_at": time.time(), "run_id": self.log.run_id,
                "purpose": f.get("purpose", ""),
            }
            saved.append(entry)
            # SSOT: multi-file zápis není transakce. Durable evidence vzniká po každém
            # jednotlivém atomickém write ještě před zahájením dalšího souboru.
            self.log.save_json("manifests", "out_write_journal", {"saved": saved, "out_dir": out_dir})
            self.progress_event.emit(ProgressEvent("Ukládání", completed=i + 1, total=len(files), unit="souborů", detail=rel))
            self.subprogress.emit(int((i + 1) * 100 / max(1, len(files))))
        self.log.save_json("manifests", "out_saved_map", {"saved": saved, "out_dir": out_dir})
        self._finish_file_delivery(files, saved=saved)
        return {"saved": saved}

    def _finish_file_delivery(self, files, *, saved=(), dry_run=False):
        step_id = getattr(self, "_delivery_step_id", "")
        if not step_id:
            return
        record = self.log.record_validation(
            step_id=step_id, target_type="delivery", target_id=self.log.run_id,
            validator="output_writes", status="passed",
            evidence={"expected": [f["path"] for f in files], "written": list(saved),
                      "dry_run": dry_run, "functionality_verified": False},
        )
        self.log.bundle.update_step(step_id, status="dry_run" if dry_run else "completed",
                                    finished_at=record["timestamp"], progress=100)

    def _write_missing_files_report(self, skipped_files: List[Dict[str, Any]]) -> Optional[str]:
        if not skipped_files:
            return None
        out_dir = self.cfg.out_dir
        ensure_dir(out_dir)
        report_path = safe_join_under_root(out_dir, "MISSINGFILES.md")
        lines: List[str] = [
            "# MISSINGFILES",
            "",
            "Tyto soubory byly součástí výstupní struktury, ale Kájovo NG je v A3 automaticky nedodává.",
            "",
        ]
        for item in skipped_files:
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            purpose = str(item.get("purpose") or "").strip() or "N/A"
            reason = str(item.get("reason") or "typ výstupu není automaticky generován").strip()
            lines.append(f"- path: `{path}`")
            lines.append(f"  - expected_content: {purpose}")
            lines.append(f"  - důvod: {reason}")
        with open(report_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        self._log_debug(f"A3: wrote missing files report -> {report_path} ({len(skipped_files)} entries)")
        return report_path

    def _create_response(self, client, payload, *, attempt=0, measurement=None):
        from .cost_context_report import CostContextReport
        if attempt:
            payload = copy.deepcopy(payload)
            payload.setdefault("metadata", {})["kajovo_repair_attempt"] = str(attempt)
        prepare_payload(payload)
        if self.cfg.mode in ("GENERATE", "MODIFY"):
            apply_quality(payload, self.cfg.maximum_quality)
        cost_report = CostContextReport(self.log.paths.run_dir)
        cost_payload = {**payload, "background": True, "store": True} if self._response_journal is not None else payload
        if measurement is not None:
            from .context_compiler import content_hash
            measurement = {**measurement, "request_hash": content_hash(cost_payload)}
        cost_report.record(cost_payload, measurement=measurement, status="submitting")
        self._progress_stage = getattr(self, "_progress_stage", self.cfg.mode)
        self.progress_event.emit(ProgressEvent(self._progress_stage, "waiting", detail="Čekám na dokončení odpovědi v OpenAI Responses API.", source="api"))
        try:
            if self._response_journal is not None:
                labels = {"queued": "Čeká ve frontě", "in_progress": "API zpracovává zadání",
                          "cancelling": "API potvrzuje zrušení generace",
                          "connection_error": "Spojení nedostupné; opakuji kontrolu stejné odpovědi"}
                response = self._response_journal.execute(
                    client, payload, stopped=lambda: self._stop, cancelled=lambda: self._cancel_response,
                    progress=lambda state, elapsed: self.progress_event.emit(ProgressEvent(
                        self._progress_stage, "waiting", detail=f"{labels[state]} · sledování {elapsed} s", source="api")),
                )
            else:
                response = client.create_response(payload)
        except ContractError as exc:
            response = getattr(exc, "response", None)
            if not isinstance(response, dict):
                raise
        cost_report.record(cost_payload, response=response)
        self.progress_event.emit(ProgressEvent(self._progress_stage, detail="Odpověď přijata z OpenAI Responses API; lokálně ověřuji výsledek.", source="api"))
        self.log.save_json("responses", f"received_{response.get('id', 'NOID')}", response)
        if response.get("status") not in (None, "completed") or response.get("error"):
            from .contracts import RemoteResponseError
            raise RemoteResponseError(response)
        validate_output(response, payload)
        return response

    # Režim GENERATE.
    def _run_a_generate(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        # ReRun se známou strukturou přeskočí A1 a A2.
        plan = {}
        a3_model = self._generate_model("A3")
        files: List[Dict[str, Any]] = []
        skipped_a3_deliverables: List[Dict[str, Any]] = []
        auto_skip_image_exts = {".png", ".jpg", ".jpeg"}
        if self.cfg.resume_files and not self.cfg.preparation_snapshot:
            if self.cfg.send_as_c:
                raise ContractError("Starý ReRun neobsahuje společnou specifikaci. Opakujte dávku v panelu BATCH nebo spusťte nový A1/A2.")
            self._set(10, 0, "ReRun: používám uloženou strukturu A2; A1/A2 se neopakují.", stage="ReRun")
            struct = {"contract": "A2_STRUCTURE", "files": self.cfg.resume_files}
            resp2_id = self.cfg.resume_prev_id or self.cfg.response_id or None
            try:
                self.log.save_json(
                    "manifests",
                    f"resume_structure_{ts_code()}",
                    {"resume_files": self.cfg.resume_files, "resume_prev_id": resp2_id},
                )
            except Exception:
                pass

            files_raw = struct.get("files", []) or []
            if not files_raw:
                raise ContractError("GENERATE ReRun: uložená struktura neobsahuje žádný výstupní soubor.")
            validate_paths(files_raw)
            for f in files_raw:
                self._check_stop()
                path = f.get("path")
                if not isinstance(path, str) or not path:
                    continue
                if path in (self.cfg.skip_paths or []):
                    continue
                ext = os.path.splitext(path)[1].lower()
                if f.get("kind") == "binary" or ext in auto_skip_image_exts:
                    skipped_a3_deliverables.append(
                        {
                            "path": path,
                            "purpose": f.get("purpose", ""),
                            "language": f.get("language", ""),
                            "reason": "binární nebo automaticky negenerovaný typ výstupu",
                        }
                    )
                    self._log_debug(f"A3: skipping generated image extension {ext} ({path})")
                    continue
                if ext in (self.cfg.skip_exts or []):
                    skipped_a3_deliverables.append({
                        "path": path, "purpose": f.get("purpose", ""),
                        "language": f.get("language", ""),
                        "reason": f"typ {ext or 'bez přípony'} je vyloučen z automatického generování",
                    })
                    self._log_debug(f"A3: skipping due to extension {ext} ({path})")
                    continue
                if path in (self.cfg.skip_paths or []):
                    self._log_debug(f"A3: skipping already completed {path}")
                    continue
                files.append(f)
        else:
            a1_text = self.cfg.prompt or ""
            if self.cfg.recovery_instruction:
                a1_text += self._recovery_suffix()
            a1_text = self._with_diag_text(self._append_io_reference(
                a1_text, self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)))
            note = self._in_dir_fallback_note()
            if note:
                a1_text += "\n\n" + note
            input_files, input_images = self._build_input_attachments(client, self._input_file_ids())
            plan, struct, resp2_id = prepare_delivery(
                self, client, "GENERATE", base_prev_id, a1_text, input_files, input_images, self._fs_tools)
            if self.cfg.send_as_c:
                selected = [f["path"] for f in struct["files"]
                            if f["kind"] == "text" and os.path.splitext(f["path"])[1].lower() not in (self.cfg.skip_exts or [])
                            and f["path"] not in (self.cfg.skip_paths or [])]
                if selected:
                    manifest = build_batch_manifest(
                        self.log.run_id, self.cfg.prompt, plan, struct, a3_model,
                        self.cfg.temperature if self._model_caps(a3_model).get("supports_temperature", False) else None,
                        selected, requirements=self._delivery_snapshot["requirements"],
                        maximum_quality=self.cfg.maximum_quality, recovery_instruction=self.cfg.recovery_instruction)
                    return self._submit_generate_batch(client, manifest)

            try:
                self.log.save_json(
                    "manifests",
                    f"resume_structure_{ts_code()}",
                    {"resume_files": struct.get("files", []) or [], "resume_prev_id": resp2_id},
                )
            except Exception:
                pass

            files_raw = struct.get("files", []) or []
            if not files_raw:
                raise ContractError("GENERATE: A2_STRUCTURE neobsahuje žádný výstupní soubor.")
            for f in files_raw:
                self._check_stop()
                path = f.get("path")
                if not isinstance(path, str) or not path:
                    continue
                if path in (self.cfg.skip_paths or []):
                    continue
                ext = os.path.splitext(path)[1].lower()
                if f.get("kind") == "binary" or ext in auto_skip_image_exts:
                    skipped_a3_deliverables.append(
                        {
                            "path": path,
                            "purpose": f.get("purpose", ""),
                            "language": f.get("language", ""),
                            "reason": "binární nebo automaticky negenerovaný typ výstupu",
                        }
                    )
                    self._log_debug(f"A3: skipping generated image extension {ext} ({path})")
                    continue
                if ext in (self.cfg.skip_exts or []):
                    skipped_a3_deliverables.append({
                        "path": path, "purpose": f.get("purpose", ""),
                        "language": f.get("language", ""),
                        "reason": f"typ {ext or 'bez přípony'} je vyloučen z automatického generování",
                    })
                    self._log_debug(f"A3: skipping due to extension {ext} ({path})")
                    continue
                if path in (self.cfg.skip_paths or []):
                    self._log_debug(f"A3: skipping already completed {path}")
                    continue
                files.append(f)

        total_files = len(files)
        base_a3_prev_id = str(resp2_id or "")
        out_files: List[Dict[str, Any]] = []
        for idx, f in enumerate(files, start=1):
            self._check_stop()
            path = f.get("path")
            self._progress_stage = "A3"
            self.progress_event.emit(ProgressEvent("A3", completed=idx - 1, total=total_files, unit="souborů", detail=str(path)))
            self._set(30 + int(45 * (idx - 1) / max(1, len(files))), 0, f"A3: generuji soubor {path} ({idx}/{total_files})")
            content, _last_resp_id = self._gen_file_chunks(
                client,
                prev_id=base_a3_prev_id,
                contract="A3_FILE",
                path=path,
                action=None,
                diag_file_ids=diag_file_ids,
                tools=self._fs_tools,
                model_override=a3_model,
            )
            out_files.append({"path": path, "content": content, "purpose": f.get("purpose", "")})
            self.subprogress.emit(int(idx * 100 / max(1, total_files)))
            self.progress_event.emit(ProgressEvent("A3", completed=idx, total=total_files, unit="souborů", detail=str(path)))

        self._verify_completed_files()
        saved_map = self._save_out_files(out_files)
        missing_report = self._write_missing_files_report(skipped_a3_deliverables)
        missing_deliverables = [item.get("path") for item in skipped_a3_deliverables if item.get("path")]
        no_changes = not out_files and not missing_deliverables
        if missing_deliverables:
            self.log.update_state({"missing_deliverables": missing_deliverables})
        elif no_changes:
            self.log.update_state({"no_changes": True, "written_files": []})
        return {
            "mode": "GENERATE",
            "plan": plan,
            "structure": struct,
            "saved": saved_map,
            "status": "partial" if missing_deliverables else "files_complete_unverified",
            "no_changes": no_changes,
            "response_id": resp2_id,
            "last_response_id": self._final_response_id or resp2_id,
            "missing_files_report": missing_report,
            "missing_deliverables": missing_deliverables,
        }

    # Odeslání souborových úloh po živé přípravě.
    def _submit_generate_batch(self, client, manifest):
        from .cost_context_report import CostContextReport
        from .recoverable_artifacts import load_run_state
        current_state = load_run_state(self.log.paths.run_dir)
        if current_state.get("submission_unknown") or current_state.get("status") == "submission_unknown":
            raise ContractError("Předchozí neurčitý submit musí být dohledán před dalším odesláním.")
        if manifest.get("version") != 3:
            raise ContractError("Nové odeslání legacy snapshotové dávky je zakázáno; je nutná explicitní příprava FileContext.")
        report = CostContextReport(self.log.paths.run_dir)
        for row, measurement in zip(manifest["requests"], manifest["cost_context_reports"], strict=True):
            report.record(row["body"], custom_id=row["custom_id"],
                          path=manifest["expected"][row["custom_id"]], measurement=measurement)
        total_input = sum(r["input_tokens"] for r in manifest["cost_context_reports"])
        self.progress_event.emit(ProgressEvent("Kontext BATCH", detail=
            f"{len(manifest['requests'])} úloh · odhad vstupu {total_input:,} tokenů · "
            f"{manifest['requests'][0]['body']['model']} · podrobnosti v cost_context_report.json"))
        self._verify_completed_files()
        completed = {path: value for path, value in (getattr(self.cfg, "completed_hashes", None) or {}).items()
                     if path in (self.cfg.skip_paths or []) and path in manifest.get("omitted", [])}
        if completed:
            manifest["completed_hashes"] = completed
            manifest["omitted"] = [path for path in manifest["omitted"] if path not in completed]
        encode_requests(manifest)
        for row in manifest["requests"]:
            client.validate_access(row["body"], batch=True)
        data = encode_requests(manifest)
        path = os.path.join(self.log.paths.requests_dir, "generate_batch.jsonl")
        with open(path, "wb") as stream:
            stream.write(data)
        self.log.update_state({"generate_batch": manifest, "status": "batch_prepared"})
        self._check_stop()
        self._set(45, 0, "Lokálně kontroluji a nahrávám pracovní BATCH…", stage="Příprava BATCH")
        # Nahrává se přímo skutečný pracovní JSONL. upload_file provede pouze
        # lokální validaci obsahu a nevytváří žádnou zkušební dávku.
        uploaded = with_retry(lambda: client.upload_file(path, purpose="batch"), self.settings.retry, self.breaker)
        evidence = {
            "batch_input_file_id": uploaded["id"],
            "submission_input_file_id": uploaded["id"],
            "submission_endpoint": "/v1/responses",
            "submission_jsonl_sha256": hashlib.sha256(data).hexdigest(),
        }
        self.log.update_state(evidence)
        self._set(55, 0, "Odesílám pracovní dávku…", stage="BATCH SUBMIT")
        self.log.update_state({"submission_unknown": True})
        batch = submit_verified_batch(client, uploaded["id"], manifest["requests"])
        self.log.update_state({"batch_id": batch["id"], "status": "batch_pending", "submission_unknown": False,
                               "batch_records": {batch["id"]: batch}})
        self.log.save_json("manifests", "generate_batch_created", batch)
        self._set(100, 0, "Příprava dokončena; souborové úlohy čekají na zpracování dávky.", stage="Čekání na dávku")
        return {"mode": manifest.get("mode", "GENERATE"), "batch_id": batch["id"], "input_file_id": uploaded["id"],
                "status": "batch_pending", "files": len(manifest["expected"])}

    # Režim MODIFY.
    def _run_b_modify(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        self._set(8, 0, "Skenuji a nahrávám vstupní projekt IN…", stage="Vstupní data")
        root = self.cfg.in_dir
        context_path = self.log.find_json("manifests", "response_modify_context") if self._response_journal else None
        if context_path:
            context = json.loads(Path(context_path).read_text(encoding="utf-8"))
            items = [SimpleNamespace(**item) for item in context["items"]]
            up_items = [item for item in items if item.uploadable]
            tools, supports_fs, vs_id = context["tools"], context["supports_fs"], context["vs_id"]
            b_text, b_input_files, b_input_images = context["text"], context["input_files"], context["input_images"]
            self._file_name_cache.update(context["file_names"])
            self._fs_tools = tools
        else:
            root_name = os.path.basename(os.path.abspath(root))

            items = scan_tree(
                root,
                root_name,
                deny_dirs=[".git", "venv", ".venv", "LOG", "cache", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"],
                deny_exts=self.settings.security.deny_extensions_in,
                allow_exts=self.settings.security.allow_extensions_in,
                deny_globs=self.settings.security.deny_globs_in,
                allow_globs=self.settings.security.allow_globs_in,
                allow_sensitive=self.settings.security.allow_upload_sensitive,
            )
            manifest = build_manifest(root, items, extra={"project": self.cfg.project})
            manifest_path = os.path.join(self.log.paths.manifests_dir, f"mirror_manifest_{ts_code()}.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            mf_up = with_retry(lambda: client.upload_file(manifest_path, purpose="user_data"), self.settings.retry, self.breaker)
            manifest_file_id = mf_up["id"]
            self._remember_file_name(manifest_file_id, os.path.basename(manifest_path))
            self._log_debug(f"Mirror manifest uploaded: {manifest_file_id}")

            uploaded: List[Tuple[str, str]] = []
            up_items = [it for it in items if it.uploadable]
            for i, it in enumerate(up_items):
                self._check_stop()
                self._progress_stage = "Upload"
                self.progress_event.emit(ProgressEvent("Upload", completed=i, total=len(up_items), unit="souborů", detail=it.rel_path))
                self._log_debug(f"Upload mirror file: {it.rel_path}")
                up = with_retry(lambda p=it.abs_path: client.upload_file(p, purpose="user_data"), self.settings.retry, self.breaker)
                uploaded.append((it.rel_path, up["id"]))
                self.subprogress.emit(int((i + 1) * 100 / max(1, len(up_items))))
                self.progress_event.emit(ProgressEvent("Upload", completed=i + 1, total=len(up_items), unit="souborů", detail=it.rel_path))
                self._remember_file_name(up["id"], os.path.basename(it.abs_path))
                try:
                    self.log.event("upload.mirror", {"path": it.rel_path, "abs": it.abs_path, "file_id": up["id"], "bytes": it.size})
                except Exception:
                    pass

            self.log.save_json("manifests", "mirror_manifest", {"manifest_file_id": manifest_file_id, "uploaded": uploaded, "manifest": manifest})

            tools: Optional[List[Dict[str, Any]]] = None
            vs_id: Optional[str] = None
            vs_ids: List[str] = list(self._vector_store_ids or [])
            supports_fs = bool(self.cfg.model_caps.get("supports_file_search", False)) and bool(self.cfg.use_file_search)

            if supports_fs:
                try:
                    self._set(18, 0, "Vytvářím a indexuji vector store pro file_search…", stage="Indexace")
                    vs = with_retry(lambda: client.create_vector_store(f"{(self.cfg.project or root_name)}{ts_code()}"), self.settings.retry, self.breaker)
                    vs_id = vs.get("id")
                    if vs_id:
                        vs_file_ids: List[str] = []
                        for rel, fid in uploaded[:2000]:
                            self._check_stop()
                            vs_file = with_retry(
                            lambda v=vs_id, f=fid, r=rel: client.add_file_to_vector_store(v, f, attributes={"source_path": os.path.join(root, r)}),
                                self.settings.retry,
                                self.breaker,
                            )
                            try:
                                vs_file_id = str(vs_file.get("id") or "")
                                if vs_file_id:
                                    vs_file_ids.append(vs_file_id)
                            except Exception:
                                pass
                        mf_vs_file = with_retry(lambda: client.add_file_to_vector_store(vs_id, manifest_file_id, attributes={"source": "mirror_manifest"}), self.settings.retry, self.breaker)
                        try:
                            mf_vs_id = str(mf_vs_file.get("id") or "")
                            if mf_vs_id:
                                vs_file_ids.append(mf_vs_id)
                        except Exception:
                            pass
                        if vs_file_ids:
                            self._wait_vector_store_files(client, vs_id, vs_file_ids)
                        vs_ids.append(vs_id)
                        self._vector_store_ids.append(vs_id)
                except Exception as e:
                    supports_fs = bool(vs_ids)
                    tools = None
                    vs_id = None
                    try:
                        self.log.exception("vector_store", e)
                        self.log.update_state({"modify_context": {
                            "new_vector_store": "failed", "direct_inputs": True,
                            "existing_vector_stores": list(vs_ids), "error": str(e),
                        }})
                    except Exception:
                        pass
                    self.progress_event.emit(ProgressEvent(
                        "Indexace", detail="Nová indexace selhala; aktuální IN zůstává připojen přímo jako vstupní soubory."
                    ))

            if supports_fs and vs_ids:
                seen = set()
                uniq_ids: List[str] = []
                for vid in vs_ids:
                    if vid and vid not in seen:
                        uniq_ids.append(vid)
                        seen.add(vid)
                tools = [{"type": "file_search", "vector_store_ids": uniq_ids}]
                if tools:
                    self._fs_tools = tools

            b_text = self.cfg.prompt or ""
            if self.cfg.recovery_instruction:
                b_text += self._recovery_suffix()
            b_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids + [manifest_file_id] + [fid for _, fid in uploaded])
            b_input_files, b_input_images = self._build_input_attachments(
                client, self._input_file_ids() + [manifest_file_id] + [fid for _, fid in uploaded])
            b_text = self._with_diag_text(self._append_io_reference(b_text, b_ref_files))
            if self._response_journal:
                self.log.save_json("manifests", "response_modify_context", {
                    "items": [vars(item) for item in items], "tools": tools, "supports_fs": supports_fs,
                    "vs_id": vs_id, "text": b_text, "input_files": b_input_files,
                    "input_images": b_input_images, "file_names": self._file_name_cache,
                })
        plan, struct, resp2_id = prepare_delivery(
            self, client, "MODIFY", base_prev_id, b_text, b_input_files, b_input_images,
            tools if supports_fs else None)

        touched_raw = struct.get("touched_files", []) or []
        validate_paths(touched_raw)
        validate_modify_sources(struct, root, items, self.cfg.skip_paths or [])
        if any(item.get("action") not in ("add", "modify") for item in touched_raw):
            raise ContractError("B2: action musí být add nebo modify.")
        if not touched_raw:
            self._verify_completed_files()
            self.log.update_state({"no_changes": True, "written_files": []})
            self.progress_event.emit(ProgressEvent("B2", detail="Nebyla navržena žádná změna; do OUT se nebude zapisovat."))
            return {
                "mode": "MODIFY", "plan": plan, "structure": struct,
                "saved": {"saved": []}, "written_files": [], "no_changes": True,
                "status": "dry_run" if self.settings.dry_run_modify else "completed",
                "dry_run": bool(self.settings.dry_run_modify),
                "response_id": resp2_id, "last_response_id": self._final_response_id or resp2_id,
            }
        touched = []
        omitted = []
        for tf in touched_raw:
            path = tf.get("path", "")
            if not path:
                continue
            if path in (self.cfg.skip_paths or []):
                continue
            ext = os.path.splitext(path)[1].lower()
            if tf.get("kind") == "binary" or ext in (self.cfg.skip_exts or []):
                self._log_debug(f"B3: skipping due to extension {ext} ({path})")
                omitted.append(path)
                continue
            if path in (self.cfg.skip_paths or []):
                self._log_debug(f"B3: skipping already completed {path}")
                continue
            touched.append(tf)

        originals = {}
        source_items = {item.rel_path: item for item in up_items}
        overwrite_hashes = {}
        for file in touched:
            path = file["path"]
            if file["action"] == "modify" and path not in source_items:
                raise ContractError(f"B3: měněný soubor není dostupný ve schváleném IN: {path}")
            if file["action"] == "add" and os.path.lexists(safe_join_under_root(root, path)):
                raise ContractError(f"B3: přidávaný soubor již existuje v IN: {path}")
            relevant = {path, *file.get("dependencies", [])}
            for source in relevant:
                if source in (self.cfg.skip_paths or []):
                    continue
                item = source_items.get(source)
                if item is None:
                    continue
                source_path = safe_join_under_root(root, source)
                if sha256_file(source_path) != item.sha256:
                    raise ContractError(f"IN se od skenu změnil: {source}")
                with open(source_path, encoding="utf-8") as stream:
                    originals[source] = stream.read()
            target = safe_join_under_root(self.cfg.out_dir, path)
            if os.path.isfile(target):
                overwrite_hashes[path] = sha256_file(target)
        originals_path = self.log.find_json("manifests", "response_modify_originals") if self._response_journal else None
        if originals_path:
            saved_originals = json.loads(Path(originals_path).read_text(encoding="utf-8"))
            originals, overwrite_hashes = saved_originals["originals"], saved_originals["overwrite_hashes"]
        elif self._response_journal:
            self.log.save_json("manifests", "response_modify_originals", {
                "originals": originals, "overwrite_hashes": overwrite_hashes,
            })
        self._delivery_originals = originals
        self._delivery_overwrite_hashes = overwrite_hashes
        if self.cfg.send_as_c and touched:
            manifest = build_batch_manifest(
                self.log.run_id, self.cfg.prompt, plan, struct, self.cfg.model,
                self.cfg.temperature, [file["path"] for file in touched],
                requirements=self._delivery_snapshot["requirements"],
                maximum_quality=self.cfg.maximum_quality, mode="MODIFY", originals=originals,
                recovery_instruction=self.cfg.recovery_instruction)
            manifest["overwrite_hashes"] = overwrite_hashes
            manifest["dry_run"] = bool(self.settings.dry_run_modify)
            manifest["versing"] = bool(self.cfg.versing)
            return self._submit_generate_batch(client, manifest)

        total_files = len(touched)
        chain_prev_id = str(resp2_id or "")
        out_files: List[Dict[str, Any]] = []
        generation_order = [row for row in touched_raw if row in touched or row["path"] in (self.cfg.skip_paths or [])]
        for i, tf in enumerate(generation_order, start=1):
            self._check_stop()
            path = tf.get("path", "")
            if path in (self.cfg.skip_paths or []):
                if self._response_journal and path in self._response_file_ids:
                    chain_prev_id = self._response_file_ids[path]
                continue
            action = tf.get("action", "modify")
            self._progress_stage = "B3"
            self.progress_event.emit(ProgressEvent("B3", completed=i - 1, total=total_files, unit="souborů", detail=str(path)))
            self._set(50 + int(35 * (i - 1) / max(1, len(touched))), 0, f"B3: {'upravuji' if action == 'modify' else 'přidávám'} {path} ({i}/{total_files})")
            content, last_resp_id = self._gen_file_chunks(
                client,
                prev_id=chain_prev_id,
                contract="B3_FILE",
                path=path,
                action=action,
                diag_file_ids=diag_file_ids,
                tools=tools if supports_fs else None,
            )
            if last_resp_id:
                chain_prev_id = last_resp_id
            out_files.append({"path": path, "content": content})
            self.subprogress.emit(int(i * 100 / max(1, total_files)))
            self.progress_event.emit(ProgressEvent("B3", completed=i, total=total_files, unit="souborů", detail=str(path)))

        self._verify_completed_files()
        saved_map = self._save_out_files(out_files)
        if omitted:
            self.log.update_state({"missing_deliverables": omitted})
        return {"mode": "MODIFY", "plan": plan, "structure": struct, "saved": saved_map,
                "response_id": resp2_id, "vector_store_id": vs_id, "supports_file_search": supports_fs,
                "status": "partial" if omitted else "dry_run" if saved_map.get("dry_run") else "files_complete_unverified",
                "dry_run": bool(saved_map.get("dry_run")), "missing_deliverables": omitted}

    # Režim QA.
    def _run_qa(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        self._set(10, 0, "QA: odesílám dotaz…", stage="QA")
        note = self._in_dir_fallback_note()
        input_text = self.cfg.prompt or ""
        if self.cfg.recovery_instruction:
            input_text += self._recovery_suffix()
        if note:
            input_text = f"{input_text}\n\n{note}"
        qa_note = "Pozn.: Vrat pouze cisty text (bez markdownu) a neposilej zadne soubory."
        if qa_note not in input_text:
            input_text = f"{input_text}\n\n{qa_note}"
        ref_file_ids = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        input_file_ids, input_image_ids = self._build_input_attachments(client, self._input_file_ids())
        input_text = self._append_io_reference(input_text, ref_file_ids)
        input_text = self._with_diag_text(input_text)
        input_parts = self._input_parts(input_text, input_file_ids, input_image_ids)
        payload = self._payload_base(
            model=self.cfg.model,
            instructions=self._append_io_reference_instructions(
                "Jsi QA asistent. Vrat pouze cisty text bez markdownu, bez souboru.",
                ref_file_ids,
            ),
            input_parts=input_parts,
            prev_id=base_prev_id,
        )
        if self._fs_tools:
            payload["tools"] = self._fs_tools
        self._log_request_attachments("QA", ref_file_ids, input_file_ids, input_image_ids, self._vector_store_ids, self._fs_tools)
        self._log_api_action(
            "QA",
            "prepare",
            {
                "prompt_len": len(self.cfg.prompt or ""),
                "files": len(ref_file_ids),
            },
        )
        self.log.save_json(
            "requests",
            f"QA_request_{ts_code()}",
            {
                "payload": payload,
                "ui_state": self.cfg.__dict__,
            },
        )
        self._log_api_action("QA", "send", {"description": "QA request", "model": self.cfg.model})
        resp = self._create_response(client, payload)
        self.log.save_json("responses", f"QA_response_{resp.get('id','NOID')}_{ts_code()}", resp)
        self._log_api_action("QA", "receive", {"response_id": resp.get("id"), "status": resp.get("status")})
        return {"mode": "QA", "response_id": str(resp.get("id") or ""), "text": user_text(resp, payload)}

    # Režim QFILE.
    def _run_qfile(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        self._set(10, 0, "QFILE: generuji soubor…", stage="QFILE")
        self._check_stop()
        prompt = (self.cfg.prompt or "").strip()
        if not prompt:
            raise RuntimeError("QFILE: Zadání je prázdné.")
        if self.cfg.recovery_instruction:
            prompt += self._recovery_suffix()
        note = self._in_dir_fallback_note()
        if note:
            prompt = f"{prompt}\n\n{note}"
        qfile_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        qfile_input_files, qfile_input_images = self._build_input_attachments(client, self._input_file_ids())
        prompt = self._append_io_reference(prompt, qfile_ref_files)
        prompt = self._with_diag_text(prompt)

        schema = '{"contract":"A3_FILE","path":"string","chunking":{"max_lines":500,"chunk_index":0,"chunk_count":0,"has_more":false,"next_chunk_index":null},"content":"string"}'
        instructions = (
            "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
            "KRITICKÉ: content je vždy kompletní výsledné znění souboru (ne diff/patch). "
            f"CHUNK: max 500 řádků. KONTRAKT: {schema}"
        )
        gen_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        instructions = self._append_io_reference_instructions(instructions, gen_ref_files)
        instructions = self._append_io_reference_instructions(instructions, qfile_ref_files)
        input_text = (
            "Vrať kompletní obsah jednoho souboru dle zadání níže. "
            "CHUNK_INDEX=0, chunk_count=1, chunking.has_more=false (QFILE je jednorázový request). "
            "Použij cestu/path popsanou v zadání (žádný manifest). "
            f"Zadání:\n{prompt}"
        )
        payload = self._payload_base(
            model=self.cfg.model,
            instructions=instructions,
            input_parts=self._input_parts(input_text, qfile_input_files, qfile_input_images),
            prev_id=base_prev_id,
        )

        payload["text"] = file_response_format("A3_FILE", None, 0)
        chunk_props = payload["text"]["format"]["schema"]["properties"]["chunking"]["properties"]
        chunk_props["has_more"] = {"type": "boolean", "enum": [False]}
        chunk_props["chunk_count"] = {"type": "integer", "enum": [1]}
        chunk_props["next_chunk_index"] = {"type": "null"}
        if self.cfg.model_caps.get("supports_temperature", True) and not uses_reasoning_defaults(self.cfg.model):
            payload["temperature"] = 0.0
        if self._fs_tools:
            payload["tools"] = self._fs_tools
        self._log_request_attachments("QFILE", qfile_ref_files, qfile_input_files, qfile_input_images, self._vector_store_ids, self._fs_tools)

        self.log.save_json(
            "requests",
            f"QFILE_request_{ts_code()}",
            {
                "payload": payload,
                "ui_state": self.cfg.__dict__,
            },
        )
        self._log_api_action(
            "QFILE",
            "send",
            {
                "model": self.cfg.model,
                "prev_id": base_prev_id,
                "files": len(self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)),
            },
        )
        resp = self._create_response(client, payload)
        self.log.save_json("responses", f"QFILE_response_{resp.get('id','NOID')}_{ts_code()}", resp)
        self._log_api_action("QFILE", "receive", {"response_id": resp.get("id"), "status": resp.get("status")})

        raw_text = extract_text_from_response(resp)
        parsed = parse_json_strict(raw_text)
        if parsed.get("contract") != "A3_FILE":
            raise ContractError("QFILE: očekáván kontrakt A3_FILE")

        chunk = parsed.get("chunking")
        if not isinstance(chunk, dict) or chunk.get("has_more") is not False or chunk.get("chunk_index") != 0:
            raise ContractError("QFILE: chunking.has_more musí být false (jediný chunk).")

        path = parsed.get("path")
        if not isinstance(path, str) or not path:
            raise ContractError("QFILE: chybí path v odpovědi.")

        if not isinstance(parsed.get("content"), str):
            raise ContractError("QFILE: content musí být text.")
        out_files = [{"path": path, "content": parsed["content"], "purpose": "QFILE"}]
        self._set(70, 0, f"QFILE: ukládám {path}...")
        saved_map = self._save_out_files(out_files)
        step = next((row for row in reversed(self.log.bundle.steps()) if row.get("stage") == "QFILE"), {})
        self.log.record_validation(
            step_id=str(step.get("step_id") or ""),
            target_type="file_contract",
            target_id=path,
            validator="QFILE.A3_FILE",
            status="passed",
            evidence={"contract": "A3_FILE", "path": path, "chunk_count": 1},
        )
        self.log.update_state({"file_contract_valid": True, "human_verified": False})
        return {"mode": "QFILE", "response_id": str(resp.get("id") or ""), "saved": saved_map, "contract": parsed, "text": raw_text}


    # Generování souborů A3/B3.
    def _gen_file_chunks(
        self,
        client: OpenAIClient,
        prev_id: str,
        contract: str,
        path: str,
        action: Optional[str],
        diag_file_ids: List[str],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, str]:
        from .context_compiler import ContextCompiler, canonical
        from .context_budget import configure_file_request, checked_measurement
        if not getattr(self, "_delivery_snapshot", None):
            raise ContractError("Souborová generace vyžaduje úplnou kanonickou přípravu FileContext.")
        self._delivery_step_id = self.log.begin_validated_step(contract.split("_")[0], kind="file_delivery")
        compiler = ContextCompiler(self._delivery_snapshot)
        compiled = compiler.compile(path, originals=getattr(self, "_delivery_originals", None))
        gen_ref_files, gen_input_files, gen_input_images = [], [], []
        tools = None

        instructions = stage_instructions("A3" if contract == "A3_FILE" else "B3")
        chunk_index = 0
        parts: List[str] = []
        latest_response_id = ""
        declared_chunk_count = 0
        rejected_chunks = []
        step_model = str(model_override or self.cfg.model or "").strip()
        while True:
            self._check_stop()
            if contract == "A3_FILE":
                prompt = f"Vrať obsah souboru PATH={path}. Pokud je dlouhý, vrať chunk CHUNK_INDEX={chunk_index}."
            else:
                prompt = f"Vrať výsledný obsah souboru PATH={path} ACTION={action}. Pokud je dlouhý, vrať chunk CHUNK_INDEX={chunk_index}."
            if self.cfg.recovery_instruction:
                prompt += self._recovery_suffix()
            prompt += "\n" + canonical({"file_context": compiled} if not parts else {
                "file_context_hash": compiled["file_context_hash"],
                "instruction": "Zachovej implementační kontrakt prvního chunku tohoto souboru."})
            prompt += "\n" + json.dumps({"chunk_max_lines": 500}, ensure_ascii=False)
            if parts:
                prompt += "\n" + canonical({"continuation": {
                    "chunk_index": chunk_index,
                    "prefix_sha256": hashlib.sha256("".join(parts).encode("utf-8")).hexdigest(),
                    "prefix_characters": sum(len(part) for part in parts),
                    "instruction": "Pokračuj přesně za posledním potvrzeným chunkem; neopakuj prefix."}})

            payload = self._payload_base(
                model=step_model,
                instructions=instructions,
                input_parts=self._input_parts(prompt, gen_input_files, gen_input_images),
                prev_id=latest_response_id,
                supports_temperature=(step_model == self.cfg.model and self.cfg.model_caps.get("supports_temperature", True)),
            )

            payload["text"] = file_response_format(contract, path, chunk_index, action)
            if step_model == self.cfg.model and self.cfg.model_caps.get("supports_temperature", True) and not uses_reasoning_defaults(step_model):
                payload["temperature"] = 0.0

            routing = configure_file_request(payload, compiled, maximum_quality=self.cfg.maximum_quality)
            if tools:
                payload["tools"] = tools
            prepare_payload(payload)
            report = checked_measurement(payload, client, compiled=compiled)
            self.progress_event.emit(ProgressEvent(contract[:2], detail=
                f"{path} · vstup ~{report['input_tokens']:,} tokenů · {step_model} · "
                f"reasoning {payload.get('reasoning', {}).get('effort', 'bez reasoning')} · "
                f"výstupní rozpočet {payload['max_output_tokens']:,} · " + " ".join(report["warnings"])))
            self.log.save_json("manifests", f"context_{path.replace('/', '_')}_{chunk_index}",
                               {"context": compiled, "routing": routing, "measurement": report})
            if tools:
                payload["tools"] = tools
            vs_ids = []
            if tools:
                for t in tools:
                    if isinstance(t, dict) and t.get("type") == "file_search":
                        vs_ids = list(t.get("vector_store_ids") or [])
                        break
            self._log_request_attachments(contract, gen_ref_files, gen_input_files, gen_input_images, vs_ids, tools)

            self.log.save_json(
                "requests",
                f"{contract}_{path}_{chunk_index}_{ts_code()}",
                {
                    "payload": payload,
                    "ui_state": self.cfg.__dict__,
                },
                step_id=self._delivery_step_id,
            )
            self._log_api_action(
                f"{contract}:{path}",
                "send",
                {"chunk_index": chunk_index, "contract": contract, "path": path},
            )
            attempt = 0
            max_attempts = 3
            parsed = None
            last_err: Optional[Exception] = None
            while attempt < max_attempts and parsed is None:
                if attempt:
                    repair_payload = copy.deepcopy(payload)
                    repair_payload.setdefault("metadata", {})["kajovo_repair_attempt"] = str(attempt)
                    self.log.save_json("requests", f"{contract}_{path}_{chunk_index}_repair_{attempt}",
                                       {"payload": repair_payload}, step_id=self._delivery_step_id)
                try:
                    resp = self._create_response(client, payload, attempt=attempt, measurement=report)
                except OutputContractError as exc:
                    self.log.save_json("responses", f"{contract}_{path}_invalid_{chunk_index}_{attempt}", exc.response,
                                       step_id=self._delivery_step_id)
                    rejected = self.log.record_validation(
                        step_id=self._delivery_step_id, target_type="file_chunk", target_id=path,
                        validator=contract, status="failed", errors=[str(exc)],
                        evidence={"chunk": chunk_index, "attempt": attempt, "response_id": exc.response.get("id")},
                    )
                    rejected_chunks.append(rejected["validation_id"])
                    last_err = exc
                    repair = {"validation_error": str(exc),
                              "invalid_output": extract_text_from_response(exc.response),
                              "instruction": "Oprav konkrétní chybu a vrať úplný platný chunk podle původního kontraktu."}
                    payload["input"] = self._input_parts(prompt + "\n" + canonical({"repair": repair}), [], [])
                    report = checked_measurement(payload, client, compiled=compiled)
                    attempt += 1
                    continue
                resp_id = str(resp.get("id") or "")
                if resp_id:
                    latest_response_id = resp_id
                self.log.save_json("responses", f"{contract}_{resp.get('id','NOID')}_{path}_{chunk_index}_{ts_code()}", resp,
                                   step_id=self._delivery_step_id)
                self._log_api_action(
                    f"{contract}:{path}",
                    "receive",
                    {
                        "chunk_index": chunk_index,
                        "response_id": resp.get("id"),
                        "attempt": attempt + 1,
                        "contract": contract,
                        "path": path,
                    },
                )
                try:
                    parsed = parse_json_strict(extract_text_from_response(resp))
                    if parsed.get("contract") != contract:
                        raise ContractError(f"{contract} mismatch (got {parsed.get('contract')})")
                except Exception as e:
                    last_err = e
                    parsed = None
                    attempt += 1
                    if "previous_response_id" in str(e).lower():
                        self._last_prev_id_error = "Response ID je neplatné nebo expirované (API odmítlo previous_response_id). Ukončuji RUN."
                        raise
                    if attempt >= max_attempts:
                        self._log_debug(f"{contract} {path} chunk {chunk_index}: invalid/mismatched response after {attempt} attempts: {e}")
                        try:
                            self.log.event("contract.mismatch", {"contract": contract, "path": path, "chunk": chunk_index, "error": str(e)})
                        except Exception:
                            pass
                        break
                    self._log_debug(f"{contract} {path} chunk {chunk_index}: invalid JSON/contract, retrying ({attempt}/{max_attempts})")
                    continue

            if parsed is None:
                raise ContractError(f"{contract}: neplatný výstup pro {path} po {max_attempts} pokusech.") from last_err

            if parsed.get("path") != path:
                raise ContractError(f"{contract}: odpověď obsahuje jinou cestu než {path}.")
            if action is not None and parsed.get("action") != action:
                raise ContractError(f"{contract}: odpověď obsahuje jinou akci než {action}.")
            if not isinstance(parsed.get("content"), str):
                raise ContractError(f"{contract}: obsah souboru musí být text.")
            parts.append(parsed["content"])
            ch = parsed.get("chunking", {}) or {}
            validate_chunk_metadata(ch)
            count = ch.get("chunk_count", 0)
            if declared_chunk_count and count and count != declared_chunk_count:
                raise ContractError("Počet částí souboru se mezi odpověďmi změnil.")
            declared_chunk_count = count or declared_chunk_count
            chunk_label = (
                f"{path} · část {chunk_index + 1}/{declared_chunk_count}"
                if declared_chunk_count else f"{path} · část {chunk_index + 1}"
            )
            self.progress_event.emit(ProgressEvent(
                getattr(self, "_progress_stage", contract), detail=chunk_label
            ))
            if declared_chunk_count and not ch["has_more"] and chunk_index + 1 != declared_chunk_count:
                raise ContractError("Soubor skončil před deklarovaným počtem částí.")
            if not isinstance(ch, dict) or type(ch.get("chunk_index")) is not int or ch.get("chunk_index") != chunk_index:
                raise ContractError(f"{contract}: neplatné pořadí částí souboru.")
            if not isinstance(ch.get("has_more"), bool):
                raise ContractError(f"{contract}: has_more musí být boolean.")
            resp_id = str(resp.get("id") or "")
            self._log_api_action(
                f"{contract}:{path}",
                "complete",
                {
                    "chunk_index": chunk_index,
                    "response_id": resp_id,
                    "contract": contract,
                },
            )
            if not ch.get("has_more"):
                break

            next_index = ch.get("next_chunk_index")
            if type(next_index) is not int or next_index != chunk_index + 1:
                raise ContractError(f"{contract}: neplatný index následující části.")
            chunk_index = next_index
            if chunk_index > 5000:
                raise ContractError("Chunk loop guard")

        if self._response_journal:
            self._response_file_ids[path] = latest_response_id
            self.log.update_state({"response_file_ids": self._response_file_ids})
        content = "".join(parts)
        if not content.strip() and not compiled["working_context"]["implementation_contract"]["allow_empty"]:
            raise ContractError(f"{path}: prázdný soubor odporuje implementačnímu kontraktu.")
        self.log.record_validation(
            step_id=self._delivery_step_id, target_type="file_contract", target_id=path,
            validator=contract, status="passed",
            evidence={"chunks": len(parts), "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                      "response_id": latest_response_id, "written": False, "resolves": rejected_chunks},
        )
        from .recoverable_artifacts import save_artifact
        save_artifact(self.log.paths.run_dir, "generated/" + path, {
            "path": path, "content": content, "output_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "file_context_hash": compiled["file_context_hash"], "contract_hash": compiled["contract_hash"],
            "dependency_hashes": compiled["dependency_hashes"], "response_id": latest_response_id,
            "validation_status": "file_contract_validated_integration_unverified",
            "chunks": [{"index": i, "sha256": hashlib.sha256(part.encode("utf-8")).hexdigest()}
                       for i, part in enumerate(parts)],
        })
        return content, latest_response_id
