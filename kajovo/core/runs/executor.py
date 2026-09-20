from __future__ import annotations

import copy
import json
import logging
import os
import time
from pathlib import Path

from ..contracts import (
    ContractError,
    validate_paths,
)
from ..delivery_preparation import (
    validate_preparation_snapshot,
)
from ..openai_client import OpenAIClient
from ..openai_transport import SubmissionOutcomeUnknown
from ..orchestration.authorization import create_execution_authorization
from ..orchestration.contracts import canonical_sha256
from ..orchestration.repository import repository_for_logger
from ..orchestration.run_config import build_run_config_v2, run_scope_hash
from ..orchestration.source_pack import freeze_run_sources, source_context
from ..progress import ProgressEvent
from ..request_rules import validate_run_options
from ..response_journal import (
    ResponseCancelled,
    ResponseJournal,
    ResponsePending,
    SubmissionUnknown,
)
from .batch_execution import _submit_generate_batch
from .context import RunContext
from .contracts import RunStatus, WorkflowExecutor
from .generate import GenerateExecutor, _run_a_generate
from .locking import ExecutionLock
from .modify import ModifyExecutor
from .qa import QaExecutor, _run_qa
from .qfile import QfileExecutor
from .recovery import prepare_runtime
from .response_execution import split_text as split_text

WORKFLOWS: dict[str, WorkflowExecutor] = {
    "GENERATE": GenerateExecutor(), "MODIFY": ModifyExecutor(),
    "QA": QaExecutor(), "QFILE": QfileExecutor(),
}

class RunExecutor(RunContext):
    """Obecný lifecycle; implementaci režimu vlastní vybraný workflow executor."""

    def execute(self):
        self.run()

    # Kompatibilní vstupní body pro starší studio/test adaptéry. Vlastní
    # workflow logika zůstává ve specializovaných modulech.
    def _run_a_generate(self, client, diag_file_ids, base_prev_id):
        return _run_a_generate(self, client, diag_file_ids, base_prev_id)

    def _run_qa(self, client, diag_file_ids, base_prev_id):
        return _run_qa(self, client, diag_file_ids, base_prev_id)

    def _submit_generate_batch(self, client, manifest):
        return _submit_generate_batch(self, client, manifest)

    def run(self):
        lock = ExecutionLock(Path(self.log.paths.run_dir) / "execution.lock")
        if not lock.acquire():
            self.finished_err.emit("Tento běh již používá jiná instance aplikace.")
            return
        try:
            state_path = Path(self.log.state_path)
            saved_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
            if saved_state.get("status") == "submission_unknown" or saved_state.get("submission_unknown"):
                raise SubmissionUnknown("Nejasné předchozí odeslání blokuje nové operace.")
            self.transition(RunStatus.PREPARING)
            # Schválený run config je sestaven lokálně; input_ready checkpoint se
            # vytvoří až po zmrazení SourcePacku a autorizace.
            run_config_v2 = build_run_config_v2(self.cfg)
            base_scope_hash = run_scope_hash(self.cfg)

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
                    if self.cfg.preparation_snapshot.get("version") == 1:
                        validate_preparation_snapshot(
                            self.cfg.preparation_snapshot,
                            self.cfg.mode,
                            self.cfg.maximum_quality,
                        )
                    elif self.cfg.preparation_snapshot.get("version") != 2:
                        raise ContractError("Neznámá verze preparation checkpointu.")
            # Read-only Files/vector-store retrieval is allowed before the
            # first paid generative request so SOURCE_PACK_V1 can freeze exact
            # remote bytes as part of the authorized run scope.
            client = OpenAIClient(
                self.api_key, timeout_s=self.settings.response_timeout_s
            )
            client.configure_validation(self.settings)
            client.stopped = lambda: self._stop
            source_pack = freeze_run_sources(
                self.cfg, self.settings, self.log, client=client
            )
            self.source_pack = source_pack
            self.source_context = source_context(self.log, source_pack)
            scope_hash = canonical_sha256({
                "run_scope_hash": base_scope_hash,
                "source_pack_hash": source_pack.hash,
            })
            authorization = create_execution_authorization(
                self.log.run_id, run_config_v2, scope_hash
            )
            self.cfg.execution_approval_id = authorization.approval_id
            self.log.save_json(
                "manifests", "execution_authorization_v1", authorization.to_dict()
            )
            self.log.update_state(
                {
                    "ui_state": self.cfg.__dict__,
                    "run_config_v2": run_config_v2,
                    "run_scope_hash": scope_hash,
                    "source_pack_hash": source_pack.hash,
                    "source_pack_id": source_pack.pack_id,
                    "execution_authorization": authorization.to_dict(),
                    "status": "running",
                    "error": None,
                    "failure_detail": None,
                    "failed_at": None,
                    "started_at": time.time(),
                    "mode": self.cfg.mode,
                    "send_as_c": self.cfg.send_as_c,
                    "model": self.cfg.model,
                    "out_dir": self.cfg.out_dir,
                }
            )
            repository_for_logger(self.log).register_run(
                self.log.run_id,
                lineage_id=self.log.run_id,
                scope_hash=scope_hash,
                policy_hash=authorization.policy_hash,
                config=run_config_v2,
                approval_id=authorization.approval_id,
                status="running",
            )
            if self.cfg.mode in ("GENERATE", "MODIFY"):
                self._response_journal = ResponseJournal(
                    self.log, self.settings.response_poll_timeout_s
                )
                saved_state = json.loads(Path(self.log.state_path).read_text(encoding="utf-8"))
                self._response_file_ids = saved_state.get("response_file_ids", {})
                self.log.update_state({"response_transport": "background"})

            if getattr(self, "resume_generate_batch", None):
                result = self._submit_generate_batch(client, copy.deepcopy(self.resume_generate_batch))
                self.finished_ok.emit(result)
                return
            self._set(1, 0, "Lokálně ověřuji model, účet a parametry běhu…", stage="Lokální validace")
            client.prepare_run_validation(self.cfg)
            validate_run_options(self.cfg)

            if self.cfg.mode == "QFILE" and self.cfg.send_as_c:
                raise RuntimeError("QFILE nepodporuje SEND AS BATCH.")

            # Explicitní provider historii vyžadujeme pouze tehdy, když ji uživatel
            # skutečně zvolil. Datové kroky A0/A1/A2/A3 a B0/B1/B2/B3 používají
            # validované artefakty a nesmějí na previous_response_id spoléhat.
            if (
                self.cfg.response_id
                and self.cfg.model_caps.get("supports_previous_response_id") is False
            ):
                raise RuntimeError(
                    "Vybraný model nepodporuje explicitně zadané previous_response_id."
                )

            self.transition(RunStatus.REMOTE_WORK)
            diag_file_ids, base_prev_id = prepare_runtime(self, client)

            self.client = client
            self.diag_file_ids = diag_file_ids
            self.base_prev_id = base_prev_id
            result = WORKFLOWS[self.cfg.mode].execute(self)
            if self.lifecycle_status is RunStatus.REMOTE_WORK:
                self.transition(RunStatus.PROCESSING_RESPONSE)
            self.transition(RunStatus.FINALIZING)
            if self._final_response_id:
                result["last_response_id"] = self._final_response_id
                if not result.get("response_id"):
                    result["response_id"] = self._final_response_id

            final_status = "batch_pending" if result.get("batch_id") else str(result.get("status") or "completed")
            if final_status not in (
                "completed", "partial", "batch_pending", "dry_run",
                "files_complete_unverified", "plan_ready", "qfile_plan_ready",
            ):
                raise ContractError(f"Neplatný stav běhu: {final_status}")
            if final_status in ("completed", "partial", "dry_run", "files_complete_unverified"):
                self.log.update_state({"status": final_status, "completed_at": time.time()})
            else:
                self.log.clear_state_keys("completed_at")
                self.log.update_state({"status": final_status})
            if final_status == "completed":
                self.transition(RunStatus.COMPLETED)
            self.progress_event.emit(ProgressEvent("RUN", final_status))
            self.finished_ok.emit(result)
        except Exception as e:
            measurement = getattr(e, "context_report", None)
            if measurement:
                request_hash = str(measurement.get("request_hash") or "unknown")
                self.log.save_json(
                    "manifests",
                    "context_limit_block_" + request_hash[:16],
                    {
                        "status": "blocked",
                        "measurement": measurement,
                    },
                )
            msg = str(e)
            if self._last_prev_id_error:
                msg = self._last_prev_id_error
            if isinstance(e, (ResponsePending, SubmissionUnknown, SubmissionOutcomeUnknown)):
                state = "response_pending" if isinstance(e, ResponsePending) else "submission_unknown"
                self.log.update_state({"status": state, "error": str(e)})
                if isinstance(e, SubmissionOutcomeUnknown):
                    detail = {"operation": e.operation, "method": e.method,
                              "path": e.path, "request_id": e.request_id}
                    self.log.event("run.submission_unknown", detail)
                    self.log.update_state({"unknown_submission": detail})
                    snapshot = json.loads(Path(self.log.state_path).read_text(encoding="utf-8"))
                    self.log.checkpoint("submission_unknown", state_snapshot=snapshot,
                                        safe_to_continue=False,
                                        reason="Výsledek vzdálené operace je nutné nejdříve dohledat.")
                    self.log.bundle.seal()
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
                except Exception as evidence_error:
                    logging.getLogger(__name__).warning(
                        "Zápis pomocné evidence selhal: %s", evidence_error
                    )
                self.log.update_state({"status": "failed", "failed_at": time.time(), "error": str(e)})
                from ..user_errors import describe_error
                self.failure_detail.emit(describe_error(e))
                self.progress_event.emit(ProgressEvent("RUN", "failed"))
                self.finished_err.emit(msg)
        finally:
            lock.release()
