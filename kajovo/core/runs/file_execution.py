from __future__ import annotations

import copy
import hashlib
import logging
import os
from typing import TYPE_CHECKING, Any

from ..contracts import (
    ContractError,
    RemoteResponseError,
)
from ..model_registry import model_spec
from ..openai_client import OpenAIClient
from ..orchestration.projection import projection_from_file_context
from ..orchestration.work_order import freeze_order
from ..progress import ProgressEvent
from ..request_rules import uses_reasoning_defaults
from ..requirements import stage_instructions
from ..safe_config import safe_ui_state
from ..structured_output import (
    OutputContractError,
    file_content_format,
    prepare_payload,
    validate_output,
)
from ..utils import safe_join_under_root, sha256_file, ts_code

if TYPE_CHECKING:
    from .context import RunContext


def _gen_file_chunks(
    self: RunContext,
    client: OpenAIClient,
    prev_id: str,
    contract: str,
    path: str,
    action: str | None,
    diag_file_ids: list[str],
    tools: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    """Vytvoří jeden úplný soubor; technické dělení výstupu model neřídí."""
    from ..context_compiler import ContextCompiler, canonical
    from ..context_limits import checked_measurement, configure_file_request

    if not getattr(self, "_delivery_snapshot", None):
        raise ContractError("Souborová generace vyžaduje úplnou kanonickou přípravu FileContext.")
    self._delivery_step_id = self.log.begin_validated_step(
        contract.split("_")[0], kind="file_delivery"
    )
    compiler = ContextCompiler(self._delivery_snapshot)
    compiled = compiler.compile(
        path,
        originals=getattr(self, "_delivery_originals", None),
        verified_artifacts=getattr(self, "_delivery_verified_artifacts", None),
    )
    projection = projection_from_file_context(contract.split("_", 1)[0], path, compiled)
    self.log.save_json(
        "manifests",
        f"projection_{path.replace('/', '_')}",
        {**projection.to_dict(), "projection_hash": projection.hash},
    )
    step_model = str(model_override or self.cfg.model or "").strip()
    instructions = stage_instructions("A3" if contract == "A3_FILE" else "B3")
    prompt = (
        f"Vrať celé znění cílového souboru PATH={path} v jediném FILE_CONTENT_V1 objektu."
        if contract == "A3_FILE"
        else f"Vrať celé výsledné znění cílového souboru PATH={path} ACTION={action} "
             "v jediném FILE_CONTENT_V1 objektu."
    )
    if self.cfg.recovery_instruction:
        prompt += self._recovery_suffix()
    prompt += "\n" + canonical({"file_context": compiled})

    payload = self._payload_base(
        model=step_model,
        instructions=instructions,
        input_parts=self._input_parts(prompt, [], []),
        prev_id="",
        supports_temperature=(
            step_model == self.cfg.model
            and self.cfg.model_caps.get("supports_temperature", True)
        ),
    )
    payload["instructions"] += (
        "\nWire kontrakt FILE_CONTENT_V1: vrať pouze objekt s polem content. "
        "Cesta, akce a identita cíle jsou zmrazené v lokálním WorkOrderu a nesmějí být "
        "součástí ani výsledkem rozhodování modelu."
    )
    payload["text"] = file_content_format()
    if (
        step_model == self.cfg.model
        and self.cfg.model_caps.get("supports_temperature", True)
        and not uses_reasoning_defaults(step_model)
    ):
        payload["temperature"] = 0.0

    routing = configure_file_request(
        payload, compiled, maximum_quality=self.cfg.maximum_quality
    )
    if tools:
        payload["tools"] = tools
    prepare_payload(payload)

    vs_ids: list[str] = []
    for tool in tools or []:
        if isinstance(tool, dict) and tool.get("type") == "file_search":
            vs_ids = list(tool.get("vector_store_ids") or [])
            break
    self._log_request_attachments(contract, [], [], [], vs_ids, tools)

    from ..orchestration.authorization import automatic_attempt_limit

    max_attempts = automatic_attempt_limit(self.cfg)
    last_err: Exception | None = None
    rejected: list[str] = []
    repair: dict[str, Any] | None = None
    parsed: dict[str, Any] | None = None
    latest_response_id = ""

    for attempt in range(max_attempts):
        self._check_stop()
        working = copy.deepcopy(payload)
        if repair:
            working["input"] = self._input_parts(
                prompt + "\n" + canonical({"repair": repair}), [], []
            )
        prepare_payload(working)
        report = checked_measurement(working, client, compiled=compiled)
        self.progress_event.emit(ProgressEvent(
            contract[:2],
            detail=(
                f"{path} · vstup ~{report['input_tokens']:,} tokenů · {step_model} · "
                f"reasoning {working.get('reasoning', {}).get('effort', 'bez reasoning')} · "
                f"max. výstup {working['max_output_tokens']:,} · "
                + " ".join(report["warnings"])
            ),
        ))
        self.log.save_json(
            "manifests",
            f"context_{path.replace('/', '_')}_attempt_{attempt}",
            {"context": compiled, "routing": routing, "measurement": report},
        )
        expected_map = getattr(self, "_delivery_expected_target_hashes", None)
        if expected_map is not None and path not in expected_map:
            target = (
                safe_join_under_root(self.cfg.out_dir, path)
                if self.cfg.out_dir
                else ""
            )
            frozen_hash = (
                sha256_file(target)
                if target and os.path.isfile(target)
                else None
            )
            expected_map = dict(expected_map)
            expected_map[path] = frozen_hash
            self._delivery_expected_target_hashes = expected_map
            self.log.save_json(
                "manifests",
                f"target_expectation_{path.replace('/', '_')}",
                {
                    "path": path,
                    "expected_target_hash": frozen_hash,
                    "frozen_before_provider_request": True,
                },
            )
        expected_target_hash = (
            expected_map[path]
            if expected_map is not None
            else (getattr(self.cfg, "completed_hashes", None) or {}).get(path)
        )
        from .response_execution import inherited_response_order
        inherited_order = inherited_response_order(self, working, attempt)
        if inherited_order is not None and (
            inherited_order.target_path != path
            or inherited_order.expected_target_hash != expected_target_hash
        ):
            raise ContractError("Continue LIVE: cíl souboru neodpovídá původnímu WorkOrderu.")
        work_order = inherited_order or freeze_order(
            self.cfg,
            {
                "run_id": self.log.run_id,
                "step_id": self._delivery_step_id,
                "stage": contract.split("_", 1)[0],
                "route": "responses_live",
                "provider_endpoint": "/v1/responses",
                "target_id": path,
                "target_path": path,
                "expected_target_hash": expected_target_hash,
                "contract_name": "FILE_CONTENT_V1",
                "schema": working["text"]["format"]["schema"],
                "prompt": prompt,
                "request_payload": working,
                "model": step_model,
                "model_capability": self._model_caps(step_model),
                "source_snapshot": self._delivery_snapshot,
                "attempt_no": attempt + 1,
                "approval_id": (
                    getattr(self.cfg, "execution_approval_id", "")
                    or f"user-start:{self.log.run_id}"
                ),
            },
            projection.to_dict(),
        )
        self.log.save_json(
            "manifests",
            f"work_order_{contract}_{path}_attempt_{attempt}",
            {**work_order.to_dict(), "order_hash": work_order.order_hash},
            step_id=self._delivery_step_id,
        )
        self.log.save_json(
            "requests",
            f"{contract}_{path}_attempt_{attempt}_{ts_code()}",
            {
                "payload": working,
                "ui_state": safe_ui_state(self.cfg),
                "work_order_hash": work_order.order_hash,
            },
            step_id=self._delivery_step_id,
        )
        self._log_api_action(
            f"{contract}:{path}", "send",
            {
                "attempt": attempt + 1,
                "contract": "FILE_CONTENT_V1",
                "path": path,
                "work_order_hash": work_order.order_hash,
            },
        )
        self._active_work_order = work_order
        try:
            try:
                response = self._create_response(
                    client, working, attempt=attempt, measurement=report
                )
            finally:
                self._active_work_order = None
        except RemoteResponseError as exc:
            last_err = exc
            if exc.code != "max_output_tokens":
                raise
            validation = self.log.record_validation(
                step_id=self._delivery_step_id,
                target_type="file_contract",
                target_id=path,
                validator="FILE_CONTENT_V1",
                status="failed",
                errors=[str(exc)],
                evidence={
                    "attempt": attempt,
                    "response_id": exc.response_id,
                    "remote_code": exc.code,
                },
            )
            rejected.append(validation["validation_id"])
            current = int(payload.get("max_output_tokens") or 0)
            maximum = int(model_spec(step_model).get("max_output_tokens") or 0)
            if not maximum or current >= maximum:
                raise ContractError(
                    f"{path}: úplný soubor se nevešel ani do maximálního výstupu modelu; "
                    "A2 musí odpovědnost rozdělit do menších souborů."
                ) from exc
            payload["max_output_tokens"] = min(
                maximum, max(current * 2, current + 8192)
            )
            body = exc.body if isinstance(exc.body, dict) else {}
            repair = {
                "failure": "max_output_tokens",
                "previous_output": body.get("output_text") or "",
                "instruction": (
                    "Předchozí odpověď byla neúplná. Vrať znovu celý soubor od začátku "
                    "v jediném platném artefaktu; nevracej pokračování."
                ),
            }
            continue
        except OutputContractError as exc:
            last_err = exc
            response = exc.response
            validation = self.log.record_validation(
                step_id=self._delivery_step_id,
                target_type="file_contract",
                target_id=path,
                validator=contract,
                status="failed",
                errors=[str(exc)],
                evidence={
                    "attempt": attempt,
                    "response_id": response.get("id") if isinstance(response, dict) else "",
                },
            )
            rejected.append(validation["validation_id"])
            repair = {
                "validation_error": str(exc),
                "invalid_output": (
                    str(response.get("output_text") or "")
                    if isinstance(response, dict) else ""
                ),
                "instruction": (
                    "Oprav výstup a vrať znovu celý soubor v jediném artefaktu "
                    "podle původního strict kontraktu."
                ),
            }
            continue

        latest_response_id = str(response.get("id") or "")
        self.log.save_json(
            "responses",
            f"{contract}_{response.get('id', 'NOID')}_{path}_{ts_code()}",
            response,
            step_id=self._delivery_step_id,
        )
        self._log_api_action(
            f"{contract}:{path}", "receive",
            {
                "response_id": response.get("id"),
                "attempt": attempt + 1,
                "contract": contract,
                "path": path,
            },
        )
        try:
            candidate = validate_output(response, working)
            if not isinstance(candidate.get("content"), str):
                raise ContractError("FILE_CONTENT_V1: content musí být text.")
            if (
                not candidate["content"].strip()
                and not compiled["working_context"]["implementation_contract"]["allow_empty"]
            ):
                raise ContractError(
                    f"{path}: prázdný soubor odporuje implementačnímu kontraktu."
                )
            parsed = candidate
            break
        except (ContractError, ValueError, TypeError) as exc:
            last_err = exc
            validation = self.log.record_validation(
                step_id=self._delivery_step_id,
                target_type="file_contract",
                target_id=path,
                validator=contract,
                status="failed",
                errors=[str(exc)],
                evidence={"attempt": attempt, "response_id": latest_response_id},
            )
            rejected.append(validation["validation_id"])
            repair = {
                "validation_error": str(exc),
                "invalid_output": str(response.get("output_text") or ""),
                "instruction": (
                    "Oprav konkrétní validační chybu a vrať znovu celý soubor "
                    "v jediném platném artefaktu."
                ),
            }

    if parsed is None:
        self._log_debug(
            f"{contract} {path}: neplatný výstup po {max_attempts} pokusech: {last_err}"
        )
        try:
            self.log.event(
                "contract.mismatch",
                {"contract": contract, "path": path, "error": str(last_err)},
            )
        except Exception as evidence_error:
            logging.getLogger(__name__).warning(
                "Zápis pomocné evidence selhal: %s", evidence_error
            )
        raise ContractError(
            f"{contract}: neplatný výstup pro {path} po {max_attempts} pokusech."
        ) from last_err

    content = parsed["content"]
    self._log_api_action(
        f"{contract}:{path}", "complete",
        {"response_id": latest_response_id, "contract": contract},
    )
    self.progress_event.emit(ProgressEvent(
        getattr(self, "_progress_stage", contract),
        detail=f"{path} · převzat úplný obsah, ověřen JSON kontrakt a hash",
    ))
    if self._response_journal:
        self._response_file_ids[path] = latest_response_id
        self.log.update_state({"response_file_ids": self._response_file_ids})

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    self.log.record_validation(
        step_id=self._delivery_step_id,
        target_type="file_contract",
        target_id=path,
        validator=contract,
        status="passed",
        evidence={
            "sha256": digest,
            "response_id": latest_response_id,
            "written": False,
            "wire_contract": "FILE_CONTENT_V1",
            "work_order_hash": work_order.order_hash,
            "resolves": rejected,
        },
    )
    from ..recoverable_artifacts import save_artifact
    artifact = {
        "path": path,
        "content": content,
        "output_hash": digest,
        "file_context_hash": compiled["file_context_hash"],
        "contract_hash": compiler.provider_hash(path),
        "implementation_contract_hash": compiled["contract_hash"],
        "work_order_hash": work_order.order_hash,
        "dependency_hashes": compiled["dependency_hashes"],
        "response_id": latest_response_id,
        "validation_status": "verified",
        "verification_level": "wire_and_artifact",
        "chunks": [{"index": 0, "sha256": digest}],
    }
    save_artifact(
        self.log.paths.run_dir,
        "generated/" + path,
        artifact,
    )
    verified = dict(getattr(self, "_delivery_verified_artifacts", {}) or {})
    verified[path] = artifact
    self._delivery_verified_artifacts = verified
    return content, latest_response_id
