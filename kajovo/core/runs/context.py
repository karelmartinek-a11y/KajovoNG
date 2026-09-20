from __future__ import annotations

import copy
import time
from typing import Any

from ..openai_client import OpenAIClient
from ..progress import ProgressEvent
from ..response_journal import (
    ResponseJournal,
)
from . import (
    attachments,
    batch_execution,
    delivery_execution,
    diagnostics,
    file_execution,
    recovery,
    response_execution,
)
from .cancellation import CancellationToken
from .config import UiRunConfig
from .contracts import RunStatus, validate_transition
from .observability import emit_signal, record_event
from .observability import update_state as update_run_state
from .ports import EventPort


class RunContext:
    """Stav a explicitní služby jednoho běhu, bez volby a řízení workflow."""

    _delivery_step_id: str
    _runtime_diag_file_ids: list[str]
    _delivery_originals: dict[str, str]
    _delivery_overwrite_hashes: dict[str, str | None]
    _delivery_expected_target_hashes: dict[str, str | None]
    _resource_staged_files: dict[str, dict[str, Any]]
    _resource_states: dict[str, dict[str, Any]]

    def __init__(
        self,
        cfg: UiRunConfig,
        settings,
        api_key: str,
        run_logger,
        parent: object | None = None,
    ):
        del parent
        self.cfg = copy.deepcopy(cfg)
        self.cfg.maximum_quality = getattr(self.cfg, "maximum_quality", False)
        self.cfg.preparation_snapshot = getattr(self.cfg, "preparation_snapshot", None)
        self.settings = copy.deepcopy(settings)
        self.api_key = api_key
        self.log = run_logger
        self.progress = EventPort()
        self.progress_event = EventPort()
        self.subprogress = EventPort()
        self.status = EventPort()
        self.logline = EventPort()
        self.finished_ok = EventPort()
        self.finished_err = EventPort()
        self.failure_detail = EventPort()
        self.cancel_token = CancellationToken()
        self._stop = False
        self.lifecycle_status = RunStatus.CREATED
        self._cancel_response = False
        self._response_journal: ResponseJournal | None = None
        self.client: OpenAIClient
        self.diag_file_ids: list[str] = []
        self.base_prev_id: str | None = None
        self._response_file_ids: dict[str, str] = {}
        self.resume_generate_batch: dict[str, Any] | None = None
        self._delivery_snapshot: dict[str, Any] = {}
        self._last_prev_id_error: str | None = None
        self._final_response_id: str | None = None
        self._in_dir_info: dict[str, Any] | None = None
        self._fs_tools: list[dict[str, Any]] | None = None
        self._vector_store_ids: list[str] = []
        self._diag_vector_store_ids: list[str] = []
        self._diag_text: str = ""
        self._diag_zip_path: str = ""
        self._input_kind_cache: dict[str, str] = {}
        self._file_name_cache: dict[str, str] = {}
        self.source_pack: Any = None
        self.source_context: dict[str, Any] = {}
        self._delivery_verified_artifacts: dict[str, dict[str, Any]] = {}
        self._delivery_expected_target_hashes = {}
        self._resource_staged_files = {}
        self._resource_states = {}
        self._active_work_order: Any = None
        self._progress_stage: str = ""


    def transition(self, target: RunStatus) -> None:
        if target == self.lifecycle_status:
            return
        validate_transition(self.lifecycle_status, target)
        self.log.update_state({"lifecycle_status": target.value})
        self.lifecycle_status = target

    def _ts(self) -> str:
        return time.strftime("%Y%m%d %H%M%S")

    def _log_debug(self, msg: str) -> None:
        line = f"{self._ts()} | {msg}"
        emit_signal(self.logline, line, name="logline")
        record_event(self.log, "debug", {"ts": self._ts(), "msg": msg})

    def _log_api_action(self, stage: str, action: str, details: dict[str, Any] | None = None) -> None:
        ts = self._ts()
        parts = [f"{stage}: {action}"]
        if details:
            for key, value in details.items():
                if value is None:
                    continue
                parts.append(f"{key}={value}")
        line = f"{ts} | " + " | ".join(parts)
        emit_signal(self.logline, line, name="logline")
        event = {"ts": ts, "stage": stage, "action": action}
        if details:
            event.update({k: v for k, v in details.items() if v is not None})
        record_event(self.log, "api.trace", event)
        if event.get("response_id"):
            self._final_response_id = str(event.get("response_id") or "")
            patch = {"last_response_id": str(event.get("response_id")), "last_response_stage": stage}
            contract = event.get("contract") or (details.get("contract") if details else None)
            if contract in ("A2_STRUCTURE", "B2_STRUCTURE"):
                patch["last_structure_response_id"] = str(event.get("response_id"))
            if contract in ("A1_PLAN", "A2_STRUCTURE", "B1_PLAN", "B2_STRUCTURE"):
                patch["last_plan_response_id"] = str(event.get("response_id"))
            update_run_state(self.log, patch)

    def request_stop(self):
        self._stop = True
        self.cancel_token.cancel()

    def request_cancel_response(self):
        self._cancel_response = True

    def _check_stop(self):
        if self.cancel_token.is_cancelled() or self._stop or self._cancel_response:
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
        record_event(self.log, "ui.progress", {"p": p, "sp": sp, "msg": msg, "ts": self._ts()})

    _attachments_snapshot = attachments._attachments_snapshot

    _input_file_ids = attachments._input_file_ids

    _generate_model = attachments._generate_model

    _model_caps = attachments._model_caps

    _preparation_cap = attachments._preparation_cap

    _remember_file_name = attachments._remember_file_name

    _classify_input_kind = attachments._classify_input_kind

    _log_request_attachments = attachments._log_request_attachments

    _prepare_response_runtime = attachments._prepare_response_runtime

    _zip_in_dir = attachments._zip_in_dir

    _prepare_in_dir_upload = attachments._prepare_in_dir_upload

    _files_with_in_dir = attachments._files_with_in_dir

    _is_supported_input_file = attachments._is_supported_input_file

    _input_files_with_in_dir = attachments._input_files_with_in_dir

    _build_input_attachments = attachments._build_input_attachments

    _io_reference_note = attachments._io_reference_note

    _append_io_reference = attachments._append_io_reference

    _append_io_reference_instructions = attachments._append_io_reference_instructions

    _attach_diagnostics_vector_store = attachments._attach_diagnostics_vector_store

    _wait_vector_store_files = attachments._wait_vector_store_files

    _in_dir_fallback_note = attachments._in_dir_fallback_note

    _build_diag_text = diagnostics._build_diag_text

    _write_diagnostics_json = diagnostics._write_diagnostics_json

    _maybe_collect_diagnostics = diagnostics._maybe_collect_diagnostics

    _should_inline_diag_text = diagnostics._should_inline_diag_text

    _with_diag_text = diagnostics._with_diag_text

    _input_parts = response_execution._input_parts

    _payload_base = response_execution._payload_base

    _create_response = response_execution._create_response

    _recovery_suffix = recovery._recovery_suffix

    _verify_completed_files = recovery._verify_completed_files

    _ingest_prompt_if_needed = recovery._ingest_prompt_if_needed

    _gen_file_chunks = file_execution._gen_file_chunks

    _submit_generate_batch = batch_execution._submit_generate_batch

    _create_snapshot = delivery_execution._create_snapshot

    _save_out_files = delivery_execution._save_out_files

    _finish_file_delivery = delivery_execution._finish_file_delivery

    _write_missing_files_report = delivery_execution._write_missing_files_report
