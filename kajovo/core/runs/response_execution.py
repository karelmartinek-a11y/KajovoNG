from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from ..contracts import (
    ContractError,
)
from ..orchestration.errors import OrchestrationError
from ..orchestration.executor import classify_response
from ..progress import ProgressEvent
from ..request_rules import uses_reasoning_defaults
from ..requirements import apply_quality
from ..structured_output import (
    prepare_payload,
    text_format,
    validate_output,
)
from .contracts import RunStatus

if TYPE_CHECKING:
    from .context import RunContext

def split_text(text: str, max_chars: int) -> list[str]:
    if not text:
        return [""]
    if max_chars <= 0:
        return [text]
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        out.append(text[i : i + max_chars])
        i += max_chars
    return out



def _input_parts(self: RunContext, text: str, file_ids: list[str], image_file_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Sestaví vstup Responses API z textu a volitelných souborů či obrázků."""
    chunks = split_text(text, max_chars=20_000)
    if not chunks:
        chunks = [""]
    parts: list[dict[str, Any]] = []
    image_ids = [fid for fid in (image_file_ids or []) if fid]
    for i, ch in enumerate(chunks):
        content: list[dict[str, Any]] = [{"type": "input_text", "text": ch}]
        if i == 0 and file_ids:
            for fid in file_ids:
                content.append({"type": "input_file", "file_id": fid})
        if i == 0 and image_ids:
            for fid in image_ids:
                content.append({"type": "input_image", "file_id": fid})
        parts.append({"type": "message", "role": "user", "content": content})
    return parts


def _payload_base(self: RunContext,
    model: str,
    instructions: str,
    input_parts: list[dict[str, Any]],
    prev_id: str | None,
    supports_temperature: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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

def _work_order_for_payload(self: RunContext, payload: dict[str, Any], attempt: int):
    active = getattr(self, "_active_work_order", None)
    if (
        active is not None
        and getattr(active, "attempt_no", None) == attempt + 1
        and getattr(active, "model", None) == payload.get("model")
    ):
        return active

    from ..context_compiler import content_hash
    from ..orchestration.work_order import freeze_order

    stage = str(getattr(self, "_progress_stage", "") or self.cfg.mode)
    fmt = (payload.get("text") or {}).get("format") or {}
    schema = fmt.get("schema") or {}
    projection = {
        "input": payload.get("input"),
        "tools": payload.get("tools"),
        "previous_response_id": payload.get("previous_response_id"),
    }
    task_hash = content_hash({
        "stage": stage,
        "model": payload.get("model"),
        "projection": projection,
        "schema": schema,
    })
    order = freeze_order(
        self.cfg,
        {
            "run_id": self.log.run_id,
            "step_id": str(getattr(self, "_delivery_step_id", "") or stage),
            "task_id": f"{stage}:{task_hash[:20]}",
            "stage": stage,
            "route": "responses_live",
            "target_id": stage,
            "target_path": None,
            "expected_target_hash": None,
            "contract_name": str(fmt.get("name") or "TEXT_RESPONSE"),
            "schema": schema,
            "prompt": str(payload.get("instructions") or ""),
            "model": str(payload.get("model") or ""),
            "model_capability": (
                self._model_caps(str(payload.get("model") or ""))
                if hasattr(self, "_model_caps")
                else self.cfg.model_caps
            ),
            "source_snapshot": {
                "run_scope_hash": str(
                    self.log.bundle.run_record().get("run_scope_hash") or ""
                )
            },
            "attempt_no": attempt + 1,
            "approval_id": getattr(self.cfg, "execution_approval_id", "")
            or f"user-start:{self.log.run_id}",
        },
        projection,
    )
    self.log.save_json(
        "manifests",
        f"work_order_{stage}_{task_hash[:16]}_{attempt + 1}",
        {**order.to_dict(), "order_hash": order.order_hash},
    )
    return order


# Diagnostika.


def _create_response(self: RunContext, client, payload, *, attempt=0, measurement=None):
    from ..cost_context_report import CostContextReport
    if attempt:
        payload = copy.deepcopy(payload)
        payload.setdefault("metadata", {})["kajovo_repair_attempt"] = str(attempt)
    prepare_payload(payload)
    if self._response_journal is not None:
        apply_quality(payload, self.cfg.maximum_quality)
    cost_report = CostContextReport(self.log.paths.run_dir)
    cost_payload = {**payload, "background": True, "store": True} if self._response_journal is not None else payload
    if measurement is not None:
        from ..context_compiler import content_hash
        measurement = {**measurement, "request_hash": content_hash(cost_payload)}
    from ..orchestration.ledger import reserve_paid_request
    work_order = _work_order_for_payload(self, cost_payload, attempt)
    measurement = reserve_paid_request(
        self.log,
        self.cfg,
        client,
        cost_payload,
        measurement=measurement,
        work_order=work_order,
    )
    cost_report.record(cost_payload, measurement=measurement, status="submitting")
    self._progress_stage = getattr(self, "_progress_stage", self.cfg.mode)
    self.progress_event.emit(ProgressEvent(self._progress_stage, "waiting", detail="Čekám na dokončení odpovědi v OpenAI Responses API.", source="api"))
    if self.lifecycle_status is RunStatus.CREATED:
        self.transition(RunStatus.PREPARING)
    if self.lifecycle_status is RunStatus.PREPARING:
        self.transition(RunStatus.REMOTE_WORK)
    try:
        try:
            if self._response_journal is not None:
                labels = {
                    "queued": "Čeká ve frontě",
                    "in_progress": "API zpracovává zadání",
                    "cancelling": "API potvrzuje zrušení generace",
                    "connection_error": "Spojení nedostupné; opakuji kontrolu stejné odpovědi",
                }
                response = self._response_journal.execute(
                    client,
                    payload,
                    stopped=lambda: self._stop,
                    cancelled=lambda: self._cancel_response,
                    progress=lambda state, elapsed: self.progress_event.emit(
                        ProgressEvent(
                            self._progress_stage,
                            "waiting",
                            detail=f"{labels[state]} · sledování {elapsed} s",
                            source="api",
                        )
                    ),
                )
            else:
                response = client.create_response(payload)
        except ContractError as exc:
            response = getattr(exc, "response", None)
            if not isinstance(response, dict):
                raise
    except Exception as exc:
        from ..openai_transport import SubmissionOutcomeUnknown
        from ..orchestration.ledger import mark_submission, release_reservation
        from ..response_journal import SubmissionUnknown

        confirmed_id = (
            self._response_journal.confirmed_id(payload)
            if self._response_journal is not None
            else ""
        )
        if confirmed_id:
            # Polling may stop or a terminal remote failure may be raised after
            # the provider already returned a durable Response ID. That is a
            # known submission, never submission_unknown.
            mark_submission(
                self.log,
                work_order,
                confirmed_id,
                unknown=False,
            )
        elif isinstance(exc, (SubmissionUnknown, SubmissionOutcomeUnknown)):
            mark_submission(self.log, work_order, None, unknown=True)
        elif (
            getattr(exc, "request_sent", None) is False
            or getattr(exc, "status_code", None) in {400, 401, 403, 404, 422, 429}
        ):
            release_reservation(self.log, work_order)
        else:
            mark_submission(self.log, work_order, None, unknown=True)
        raise

    from ..orchestration.ledger import mark_submission, settle_usage
    provider_id = str(response.get("id") or "")
    if provider_id:
        mark_submission(self.log, work_order, provider_id, unknown=False)
        settle_usage(self.log, work_order, response)
    else:
        mark_submission(self.log, work_order, None, unknown=True)
        raise ContractError(
            "Provider nepotvrdil ID placené operace; automatický nový submit je zablokován."
        )
    cost_report.record(cost_payload, response=response)
    self.progress_event.emit(ProgressEvent(self._progress_stage, detail="Odpověď přijata z OpenAI Responses API; lokálně ověřuji výsledek.", source="api"))
    self.log.save_json("responses", f"received_{response.get('id', 'NOID')}", response)
    if response.get("error"):
        from ..contracts import RemoteResponseError
        raise RemoteResponseError(response)
    try:
        classified = classify_response(response)
    except OrchestrationError as exc:
        raise ContractError(str(exc)) from exc
    if classified.kind in {"remote_pending", "incomplete", "remote_failed"}:
        from ..contracts import RemoteResponseError
        raise RemoteResponseError(response)
    if classified.kind == "refusal":
        raise ContractError("Provider odmítl pracovní požadavek; výstup nebyl přijat.")
    if classified.kind == "tool_calls":
        raise ContractError("TOOL_CALL_UNHANDLED: task vyžaduje explicitní tool-dispatch pokračování.")
    self.transition(RunStatus.PROCESSING_RESPONSE)
    validate_output(response, payload)
    return response

# Režim GENERATE.
