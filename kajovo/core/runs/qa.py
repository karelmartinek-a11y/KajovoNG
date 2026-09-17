from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..openai_client import OpenAIClient
from ..structured_output import (
    user_text,
)
from ..utils import ts_code

if TYPE_CHECKING:
    from .context import RunContext

def _run_qa(self: RunContext, client: OpenAIClient, diag_file_ids: list[str], base_prev_id: str | None) -> dict[str, Any]:
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


class QaExecutor:
    """Samostatné workflow QA nad společnými službami běhu."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_qa(context, context.client, context.diag_file_ids, context.base_prev_id)
