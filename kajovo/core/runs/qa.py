from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..contracts import ContractError, extract_text_from_response
from ..openai_client import OpenAIClient
from ..orchestration.contracts import parse_json_strict
from ..orchestration.preparation import PreparationBlocked
from ..progress import ProgressEvent
from ..safe_config import safe_ui_state
from ..structured_output import OutputContractError, qa_answer_format, validate_output
from ..utils import ts_code

if TYPE_CHECKING:
    from .context import RunContext


def _run_qa(
    self: RunContext,
    client: OpenAIClient,
    diag_file_ids: list[str],
    base_prev_id: str | None,
) -> dict[str, Any]:
    self._set(10, 0, "Připravuji podklady k otázce…", stage="QA_INPUT")
    note = self._in_dir_fallback_note()
    input_text = self.cfg.prompt or ""
    if self.cfg.recovery_instruction:
        input_text += self._recovery_suffix()
    if note:
        input_text = f"{input_text}\n\n{note}"

    ref_file_ids = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
    input_file_ids, input_image_ids = self._build_input_attachments(
        client, self._input_file_ids()
    )
    input_text = self._append_io_reference(input_text, ref_file_ids)
    input_text = self._with_diag_text(input_text)
    evidence_ids = set(ref_file_ids + input_file_ids + input_image_ids)
    for segment in (getattr(self, "source_context", {}) or {}).get("segments", []):
        evidence_ids.update((segment["source_id"], segment["segment_id"]))
    input_text += "\n\nIdentifikátory dostupných podkladů: " + json.dumps(sorted(evidence_ids), ensure_ascii=False)
    payload = self._payload_base(
        model=self.cfg.model,
        instructions=self._append_io_reference_instructions(
            "Odpověz pouze podle dostupných podkladů. Každé tvrzení odděl do claims; "
            "evidence_ids smí odkazovat jen na skutečně dostupné identifikátory podkladů. "
            "Nejistotu přiznej v limitations. Pokud zásadní podklad chybí, vrať blocked.",
            ref_file_ids,
        ),
        input_parts=self._input_parts(input_text, input_file_ids, input_image_ids),
        prev_id=base_prev_id if self.cfg.qa_continue_conversation else None,
    )
    payload["text"] = qa_answer_format()
    if self._fs_tools:
        payload["tools"] = self._fs_tools
        payload["include"] = ["file_search_call.results"]

    self._log_request_attachments(
        "QA",
        ref_file_ids,
        input_file_ids,
        input_image_ids,
        self._vector_store_ids,
        self._fs_tools,
    )
    self.log.save_json(
        "requests",
        f"QA_request_{ts_code()}",
        {
            "payload": payload,
            "ui_state": safe_ui_state(self.cfg),
            "conversation_continuity": bool(self.cfg.qa_continue_conversation),
        },
    )
    self.progress_event.emit(ProgressEvent(
        "QA_INPUT", "completed", detail="Podklady a požadavek jsou připravené.",
        source="validation",
    ))
    self._log_api_action(
        "QA",
        "send",
        {
            "model": self.cfg.model,
            "previous_response_id": (
                base_prev_id if self.cfg.qa_continue_conversation else None
            ),
            "files": len(ref_file_ids),
            "contract": "QA_ANSWER_V2",
        },
    )
    self.progress_event.emit(ProgressEvent(
        "QA_RESPONSE", "waiting", detail="Čekáme na odpověď služby.", source="api",
    ))
    try:
        resp = self._create_response(client, payload)
        parsed = validate_output(resp, payload)
    except OutputContractError as exc:
        # Zachovej strict masku, ale vrať doménovou diagnostiku pro tento
        # sémanticky neplatný případ místo obecného JSON Schema výpisu.
        has_unsupported_claim = False
        try:
            response = getattr(exc, "response", None)
            raw = parse_json_strict(extract_text_from_response(response))
            result = raw.get("result") if isinstance(raw, dict) else None
            data = result.get("data") if isinstance(result, dict) else None
            claims = data.get("claims") if isinstance(data, dict) else None
            has_unsupported_claim = isinstance(claims, list) and any(
                isinstance(claim, dict)
                and claim.get("certainty") == "supported"
                and not claim.get("evidence_ids")
                for claim in claims
            )
        except Exception:
            pass
        if has_unsupported_claim:
            raise ContractError(
                "QA_ANSWER_V2: podložené tvrzení nemá žádné podklady."
            ) from exc
        raise
    self.progress_event.emit(ProgressEvent(
        "QA_RESPONSE", "completed", detail="Odpověď dorazila a má požadovanou podobu.",
        source="api", response_id=str(resp.get("id") or ""),
    ))
    self.progress_event.emit(ProgressEvent(
        "QA_VALIDATION", "active", detail="Ověřuji tvrzení a jejich podklady.",
        source="validation",
    ))
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise ContractError("QA_ANSWER_V2: chybí result.")
    if result.get("status") == "blocked":
        questions = result.get("questions") or []
        if not questions:
            raise ContractError("QA blocked odpověď nemá otázky.")
        raise PreparationBlocked("QA", questions)
    data = result.get("data")
    if result.get("status") != "ready" or not isinstance(data, dict):
        raise ContractError("QA_ANSWER_V2: neplatný stav odpovědi.")
    answer = data.get("answer")
    claims = data.get("claims")
    limitations = data.get("limitations")
    if (
        not isinstance(answer, str)
        or not isinstance(claims, list)
        or not isinstance(limitations, list)
    ):
        raise ContractError("QA_ANSWER_V2: neplatná datová část.")
    for output in resp.get("output", []):
        if output.get("type") == "file_search_call":
            evidence_ids.update(
                row["file_id"] for row in output.get("results") or [] if row.get("file_id")
            )
        if output.get("type") == "message":
            for part in output.get("content", []):
                evidence_ids.update(
                    row["file_id"] for row in part.get("annotations", [])
                    if row.get("type") == "file_citation" and row.get("file_id")
                )
    for claim in claims:
        if claim.get("certainty") == "supported" and not claim.get("evidence_ids"):
            raise ContractError("QA_ANSWER_V2: podložené tvrzení nemá žádné podklady.")
        unknown = set(claim.get("evidence_ids", [])) - evidence_ids
        if unknown:
            raise ContractError(f"QA_ANSWER_V2: neznámé podklady tvrzení: {sorted(unknown)}")

    self.log.save_json(
        "responses",
        f"QA_response_{resp.get('id','NOID')}_{ts_code()}",
        resp,
    )
    self.log.save_json(
        "manifests",
        f"QA_answer_{ts_code()}",
        {
            "contract": "QA_ANSWER_V2",
            "answer": answer,
            "claims": claims,
            "limitations": limitations,
            "response_id": str(resp.get("id") or ""),
        },
    )
    self.progress_event.emit(ProgressEvent(
        "QA_VALIDATION", "completed", detail="Struktura odpovědi a odkazy na dostupné podklady jsou ověřené; pravdivost tvrzení tím není potvrzena.",
        source="validation",
    ))
    self._log_api_action(
        "QA",
        "receive",
        {
            "response_id": resp.get("id"),
            "status": resp.get("status"),
            "contract": "QA_ANSWER_V2",
        },
    )
    return {
        "mode": "QA",
        "status": "completed",
        "response_id": str(resp.get("id") or ""),
        "text": answer,
        "claims": claims,
        "limitations": limitations,
        "conversation_continuity": bool(self.cfg.qa_continue_conversation),
    }


class QaExecutor:
    """QA s explicitní konverzační návazností a QA_ANSWER_V2."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_qa(
            context,
            context.client,
            context.diag_file_ids,
            context.base_prev_id,
        )
