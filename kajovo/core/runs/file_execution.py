from __future__ import annotations

import copy
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from ..contracts import (
    ContractError,
    extract_text_from_response,
    file_response_format,
    parse_json_strict,
    validate_chunk_metadata,
)
from ..openai_client import OpenAIClient
from ..progress import ProgressEvent
from ..request_rules import uses_reasoning_defaults
from ..requirements import stage_instructions
from ..structured_output import (
    OutputContractError,
    prepare_payload,
)
from ..utils import ts_code

if TYPE_CHECKING:
    from .context import RunContext

def _gen_file_chunks(self: RunContext,
    client: OpenAIClient,
    prev_id: str,
    contract: str,
    path: str,
    action: str | None,
    diag_file_ids: list[str],
    tools: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    from ..context_budget import checked_measurement, configure_file_request
    from ..context_compiler import ContextCompiler, canonical
    if not getattr(self, "_delivery_snapshot", None):
        raise ContractError("Souborová generace vyžaduje úplnou kanonickou přípravu FileContext.")
    self._delivery_step_id = self.log.begin_validated_step(contract.split("_")[0], kind="file_delivery")
    compiler = ContextCompiler(self._delivery_snapshot)
    compiled = compiler.compile(path, originals=getattr(self, "_delivery_originals", None))
    gen_ref_files: list[str] = []
    gen_input_files: list[str] = []
    gen_input_images: list[str] = []
    tools = None

    instructions = stage_instructions("A3" if contract == "A3_FILE" else "B3")
    chunk_index = 0
    parts: list[str] = []
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
        vs_ids: list[str] = []
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
        last_err: Exception | None = None
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
                    except Exception as evidence_error:
                        logging.getLogger(__name__).warning(
                            "Zápis pomocné evidence selhal: %s", evidence_error
                        )
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
    from ..recoverable_artifacts import save_artifact
    save_artifact(self.log.paths.run_dir, "generated/" + path, {
        "path": path, "content": content, "output_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "file_context_hash": compiled["file_context_hash"], "contract_hash": compiled["contract_hash"],
        "dependency_hashes": compiled["dependency_hashes"], "response_id": latest_response_id,
        "validation_status": "file_contract_validated_integration_unverified",
        "chunks": [{"index": i, "sha256": hashlib.sha256(part.encode("utf-8")).hexdigest()}
                   for i, part in enumerate(parts)],
    })
    return content, latest_response_id
