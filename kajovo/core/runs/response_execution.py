from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from ..contracts import (
    ContractError,
)
from ..progress import ProgressEvent
from ..orchestration.executor import classify_response
from ..orchestration.errors import OrchestrationError
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
    measurement = reserve_paid_request(
        self.log, self.cfg, client, cost_payload, measurement=measurement
    )
    cost_report.record(cost_payload, measurement=measurement, status="submitting")
    self._progress_stage = getattr(self, "_progress_stage", self.cfg.mode)
    self.progress_event.emit(ProgressEvent(self._progress_stage, "waiting", detail="Čekám na dokončení odpovědi v OpenAI Responses API.", source="api"))
    if self.lifecycle_status is RunStatus.CREATED:
        self.transition(RunStatus.PREPARING)
    if self.lifecycle_status is RunStatus.PREPARING:
        self.transition(RunStatus.REMOTE_WORK)
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
