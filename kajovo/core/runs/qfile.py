from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..contracts import (
    ContractError,
    extract_text_from_response,
    file_response_format,
    parse_json_strict,
)
from ..openai_client import OpenAIClient
from ..request_rules import uses_reasoning_defaults
from ..utils import ts_code

if TYPE_CHECKING:
    from .context import RunContext

def _run_qfile(self: RunContext, client: OpenAIClient, diag_file_ids: list[str], base_prev_id: str | None) -> dict[str, Any]:
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

    schema = '{"contract":"A3_FILE","path":"string","chunking":{"chunk_index":0,"chunk_count":1,"has_more":false,"next_chunk_index":null},"content":"string"}'
    instructions = (
        "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
        "KRITICKÉ: content je kompletní výsledné znění celého souboru v jediné Response "
        "(ne diff/patch, bez modelového chunkování). "
        f"KONTRAKT: {schema}"
    )
    gen_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
    instructions = self._append_io_reference_instructions(instructions, gen_ref_files)
    instructions = self._append_io_reference_instructions(instructions, qfile_ref_files)
    input_text = (
        "Vrať kompletní obsah jednoho souboru dle zadání níže v jediné Response. "
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
    step: dict[str, Any] = next(
        (row for row in reversed(self.log.bundle.steps()) if row.get("stage") == "QFILE"), {}
    )
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


class QfileExecutor:
    """Samostatné workflow QFILE nad společnými službami běhu."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_qfile(context, context.client, context.diag_file_ids, context.base_prev_id)
