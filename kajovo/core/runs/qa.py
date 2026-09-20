from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..contracts import ContractError
from ..openai_client import OpenAIClient
from ..safe_config import safe_ui_state
from ..structured_output import qa_answer_format, validate_output
from ..utils import ts_code

if TYPE_CHECKING:
    from .context import RunContext


def _run_qa(
    self: RunContext,
    client: OpenAIClient,
    diag_file_ids: list[str],
    base_prev_id: str | None,
) -> dict[str, Any]:
    self._set(10, 0, "QA: připravuji ověřitelný dotaz…", stage="QA")
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
    resp = self._create_response(client, payload)
    parsed = validate_output(resp, payload)
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise ContractError("QA_ANSWER_V2: chybí result.")
    if result.get("status") == "blocked":
        questions = result.get("questions") or []
        raise ContractError(
            "QA je zablokované: "
            + "; ".join(
                str(q.get("question") or q.get("code") or "chybí podklad")
                for q in questions
                if isinstance(q, dict)
            )
        )
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
