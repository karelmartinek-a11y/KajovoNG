from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema

from .cascade_contract import (
    CascadeValidationError,
    describe_output,
    humanize_cascade_error,
    output_machine_key,
    runtime_schema_for_step,
    step_signature,
    validate_cascade_definition,
)
from .cascade_log import CascadeLogger
from .cascade_types import CascadeDefinition, CascadeOutput, CascadeStep
from .contracts import ContractError, parse_json_strict, validate_paths
from .model_registry import model_spec
from .openai_client import OpenAIClient
from .openai_transport import SubmissionOutcomeUnknown
from .orchestration.contracts import canonical_bytes, canonical_sha256
from .orchestration.provider_operations import (
    mark_not_submitted,
    mark_submission,
    mark_submission_started,
    prepare_provider_request,
    record_usage,
)
from .orchestration.publish import (
    commit_publish,
    prepare_publish,
    recover_publish_journal,
)
from .orchestration.repository import repository_for_logger
from .orchestration.run_config import validate_run_config_v2
from .orchestration.work_order import freeze_order
from .progress import ProgressEvent
from .request_rules import validate_response_payload
from .runs.locking import ExecutionLock
from .runs.ports import EventPort
from .structured_output import (
    OutputContractError,
    resolve_schema,
    response_format,
    restore_optional_fields,
    text_format,
    validate_output,
)
from .utils import (
    atomic_write_text,
    ensure_dir,
    new_run_id,
    safe_join_under_root,
    sha256_file,
    validate_relative_path,
)

PLACEHOLDER_RE = re.compile(
    r"\{\{\s*step\.(\d+)\.(response_id|json|text|out_file_path|out_file_id)(?::([^}]+))?\s*\}\}"
)


PRESET_MANIFEST_SCHEMA: dict[str, Any] = {
    "description": "Souborový manifest pro přímé uložení do OUT.",
    "type": "object",
    "required": ["files"],
    "additionalProperties": False,
    "properties": {
        "files": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["path", "content", "encoding"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string", "enum": ["utf-8", "base64"]},
                },
            },
        }
    },
}


PRESET_PROMPTS_SCHEMA: dict[str, Any] = {
    "description": "Definice kaskády promptů.",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "version": {"type": "integer"},
        "name": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "model": {"type": "string"},
                    "instructions": {"type": "string"},
                    "input_text": {"type": "string"},
                },
                "required": ["title", "model", "instructions", "input_text"],
            },
        },
    },
    "required": ["version", "name", "steps"],
}


JSON_ONLY_DEVELOPER_MESSAGE = {
    "type": "message",
    "role": "developer",
    "content": [
        {
            "type": "input_text",
            "text": "Vrať pouze data vyhovující přesně předepsanému JSON schématu; žádný další text.",
        }
    ],
}


@dataclass
class CascadeRunConfig:
    project: str
    cascade: CascadeDefinition
    in_dir: str
    out_dir: str
    run_id: str = ""
    resume_snapshot: dict[str, Any] | None = None
    recovery_instruction: str = ""
    lineage: dict[str, Any] | None = None
    execution_approval_id: str = ""
    resume_source_dir: str = ""
    input_bindings: list[dict[str, Any]] | None = None


class CascadeRunExecutor:
    """Qt-free executor kaskády; thread lifecycle vlastní pouze UI adaptér."""

    def __init__(
        self,
        cfg: CascadeRunConfig,
        settings,
        api_key: str,
    ) -> None:
        self.progress = EventPort()
        self.progress_event = EventPort()
        self.subprogress = EventPort()
        self.status = EventPort()
        self.logline = EventPort()
        self.finished_ok = EventPort()
        self.finished_err = EventPort()
        self.failure_detail = EventPort()
        self.cfg = copy.deepcopy(cfg)
        if not self.cfg.out_dir.strip():
            self.cfg.out_dir = self.cfg.cascade.default_out_dir.strip()
        self.settings = copy.deepcopy(settings)
        self.api_key = api_key
        self._stop = False
        self.logger: CascadeLogger | None = None
        self._failed_step_index = 0
        self._runtime_cache: dict[str, Any] = {}

    def _schema_request(
        self,
        client,
        step: CascadeStep,
        idx: int,
        payload: dict[str, Any],
        attempt_no: int,
        task_suffix: str = "schema",
    ) -> dict[str, Any]:
        if self.logger is None:
            raise RuntimeError("Cascade logger není inicializovaný.")
        run_id = str(self.cfg.run_id or self.logger.run_id)
        operation_cfg = self._operation_cfg(step.model, self.cfg.execution_approval_id)
        task_id = f"{step.id}:{task_suffix}"
        projection = {
            "cascade_name": self.cfg.cascade.name,
            "step_id": step.id,
            "step_signature": step_signature(step),
            "attempt_no": attempt_no,
            "purpose": task_suffix,
            "input": payload.get("input"),
        }
        wire_format = (payload.get("text") or {}).get("format") or {}
        order = freeze_order(
            operation_cfg,
            {
                "run_id": run_id,
                "step_id": str(getattr(self, "_current_step_record_id", step.id)),
                "task_id": task_id,
                "stage": "CASCADE_SCHEMA" if task_suffix == "schema" else "CASCADE_PRODUCTION",
                "route": "responses_live",
                "provider_endpoint": "/v1/responses",
                "target_id": step.id,
                "target_path": None,
                "expected_target_hash": None,
                "contract_name": str(
                    wire_format.get("name") or "SCHEMA_PREPARATION"
                ),
                "schema": wire_format.get("schema") or {},
                "prompt": str(payload.get("instructions") or ""),
                "request_payload": payload,
                "model": step.model,
                "model_capability": model_spec(step.model),
                "source_snapshot": {
                    "step_signature": step_signature(step),
                    "cascade_name": self.cfg.cascade.name,
                    "purpose": task_suffix,
                },
                "attempt_no": attempt_no,
                "approval_id": self.cfg.execution_approval_id,
            },
            projection,
        )
        prepare_provider_request(
            self.logger,
            operation_cfg,
            client,
            payload,
            work_order=order,
        )
        mark_submission_started(self.logger, order)
        try:
            response = client.create_response(payload)
        except OutputContractError as exc:
            response = getattr(exc, "response", None)
            if not isinstance(response, dict):
                raise
        except Exception as exc:
            if isinstance(exc, SubmissionOutcomeUnknown):
                mark_submission(self.logger, order, None, unknown=True)
            elif (
                getattr(exc, "request_sent", None) is False
                or getattr(exc, "status_code", None)
                in {400, 401, 403, 404, 422, 429}
            ):
                mark_not_submitted(self.logger, order)
            else:
                mark_submission(self.logger, order, None, unknown=True)
            raise
        provider_id = str(response.get("id") or "").strip()
        if not provider_id:
            mark_submission(self.logger, order, None, unknown=True)
            raise SubmissionOutcomeUnknown(
                "cascade_schema",
                "POST",
                "/v1/responses",
            )
        mark_submission(self.logger, order, provider_id, unknown=False)
        record_usage(self.logger, order, response)
        self.logger.save_json(
            "responses",
            f"cascade_{canonical_sha256(task_suffix)[:16]}_{idx:02d}_attempt_{attempt_no}",
            response,
            step_id=str(getattr(self, "_current_step_record_id", step.id)),
        )
        return response

    def _operation_cfg(self, model: str, approval_id: str):
        return SimpleNamespace(
            mode="CASCADE",
            model=model,
            send_as_c=False,
            maximum_quality=False,
            auto_repair="within_approval",
            verification_profile_ids=[],
            stop_after_plan=False,
            dry_run=False,
            execution_approval_id=approval_id,
            project=self.cfg.project,
            prompt=self.cfg.cascade.name,
            in_dir=self.cfg.in_dir,
            out_dir=self.cfg.out_dir,
            attached_file_ids=[],
            input_file_ids=[],
            attached_vector_store_ids=[],
            qfile_output_path="",
            qfile_output_format="",
            qfile_suggest_path=False,
            qa_continue_conversation=False,
            response_id="",
        )

    def _register_operation_run(
        self,
        run_id: str,
        approval_id: str,
        input_artifacts: list[dict[str, Any]],
    ) -> None:
        if self.logger is None:
            raise RuntimeError("Cascade logger není inicializovaný.")
        run_config = {
            "version": 2,
            "workflow": "CASCADE",
            "execution": "live",
            "quality": "standard",
            "model_bindings": [
                {"stage": step.id, "model": step.model}
                for step in self.cfg.cascade.steps
            ],
            "auto_repair": "within_approval",
            "verification_profile_ids": [],
            "stop_after_plan": False,
            "dry_run": False,
        }
        validate_run_config_v2(run_config)
        repo = repository_for_logger(self.logger)
        repo.register_run(
            run_id,
            lineage_id=run_id,
            scope_hash=canonical_sha256(
                {
                    "run_config_v2": run_config,
                    "cascade_definition": self.cfg.cascade.to_dict(),
                    "input_artifacts": [
                        {
                            "artifact_id": row.get("artifact_id"),
                            "path_in_bundle": row.get("path_in_bundle"),
                        }
                        for row in input_artifacts
                    ],
                }
            ),
            policy_hash=canonical_sha256(run_config),
            config=run_config,
            approval_id=approval_id,
            status="running",
        )
        self.logger.update_state(
            {
                "run_config_v2": run_config,
                "execution_authorization": {
                    "approval_id": approval_id,
                    "scope_hash": canonical_sha256(
                        {
                            "cascade_definition": self.cfg.cascade.to_dict(),
                            "input_artifacts": input_artifacts,
                        }
                    ),
                },
            }
        )

    def request_stop(self):
        self._stop = True

    def _check_stop(self):
        if self._stop:
            raise RuntimeError("STOP_REQUESTED")

    def _ts(self) -> str:
        return time.strftime("%Y%m%d %H%M%S")

    def _recovery_suffix(self) -> str:
        instruction = str(self.cfg.recovery_instruction or "").strip()
        if not instruction:
            return ""
        return (
            "\n\n[NOVÁ VĚTEV - explicitní pokyn platí pouze pro tento a následující nově prováděné kroky]\n"
            + instruction
        )

    def _archive_cascade_inputs(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Archivuje rekonstruovatelné lokální vstupy před safe checkpointem."""
        if not self.logger:
            return [], []
        mappings: list[dict[str, Any]] = []
        missing: list[str] = []
        for step in self.cfg.cascade.steps:
            candidates = [
                ("input", item.id, item.value)
                for item in step.inputs
                if item.source == "local_file"
            ]
            candidates.extend(("files_local_paths", str(index), value)
                              for index, value in enumerate(step.files_local_paths or []))
            for field, input_id, value in candidates:
                if field == "files_local_paths" and PLACEHOLDER_RE.search(str(value or "")):
                    continue
                resolved = self._resolve_text(str(value or ""), {})
                binding = next((row for row in (self.cfg.input_bindings or [])
                                if (row.get("step_id"), row.get("field"), row.get("input_id"))
                                == (step.id, field, input_id)), None)
                if binding:
                    parent = Path(self.cfg.resume_source_dir).resolve()
                    frozen = Path(safe_join_under_root(parent, binding["path_in_bundle"]))
                    if sha256_file(str(frozen)) != binding["sha256"]:
                        raise ContractError("Zdrojový archiv kaskády má změněný hash.")
                    resolved = str(frozen)
                if not resolved or not Path(resolved).is_file():
                    missing.append(f"{step.id}:{input_id}")
                    continue
                artifact = self.logger.bundle.archive_artifact(
                    resolved,
                    role="user_input",
                    kind="cascade_input",
                    reconstruction_role=f"cascade:{step.id}:{field}:{input_id}",
                    metadata={"step_id": step.id, "field": field, "input_id": input_id},
                )
                mappings.append({
                    "step_id": step.id,
                    "field": field,
                    "input_id": input_id,
                    "artifact_id": artifact["artifact_id"],
                    "path_in_bundle": artifact["path_in_bundle"],
                    "sha256": artifact["sha256"],
                })
        self._frozen_cascade_inputs = mappings
        return mappings, missing

    def _archive_dynamic_inputs(self, step, context):
        """Zmrazí závislé legacy přílohy až nad výsledky jejich producentů."""
        assert self.logger is not None
        for index, expression in enumerate(step.files_local_paths or []):
            if not PLACEHOLDER_RE.search(expression):
                continue
            resolved = self._resolve_text(expression, context)
            artifact = self.logger.bundle.archive_artifact(
                resolved, role="user_input", kind="cascade_input",
                reconstruction_role=f"cascade:{step.id}:files_local_paths:{index}",
                metadata={"step_id": step.id, "field": "files_local_paths", "input_id": str(index)},
            )
            for staged in getattr(self, "_cascade_staged_files", {}).values():
                staged_path = Path(self.logger.paths.run_dir) / staged["staged_path"]
                if staged_path.resolve() == Path(resolved).resolve() and artifact["sha256"] != staged["sha256"]:
                    raise ContractError("Dynamický vstup kaskády neodpovídá archivovanému výstupu producenta.")
            self._frozen_cascade_inputs = [
                row for row in self._frozen_cascade_inputs
                if (row["step_id"], row["field"], row["input_id"])
                != (step.id, "files_local_paths", str(index))
            ]
            self._frozen_cascade_inputs.append({
                "step_id": step.id, "field": "files_local_paths", "input_id": str(index),
                **{key: artifact[key] for key in ("artifact_id", "path_in_bundle", "sha256")},
            })
            self._cascade_input_artifact_ids.append(artifact["artifact_id"])
        self.logger.update_state({"cascade_input_artifacts": self._frozen_cascade_inputs})

    def _frozen_input_path(self, step_id, field, input_id):
        assert self.logger is not None
        for row in getattr(self, "_frozen_cascade_inputs", []):
            if (row["step_id"], row["field"], row["input_id"]) != (step_id, field, input_id):
                continue
            root = Path(self.logger.paths.run_dir).resolve()
            path = (root / row["path_in_bundle"]).resolve()
            if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise ContractError("Archivovaný vstup kaskády má neplatnou integritu.")
            return str(path)
        raise ContractError(f"Kaskáda nemá zmrazený vstup {step_id}:{input_id}.")

    def _emit_status(self, p: int, sp: int, text: str) -> None:
        self.progress.emit(p)
        self.subprogress.emit(sp)
        self.status.emit(text)
        self.logline.emit(f"{self._ts()} | {text}")

    @staticmethod
    def _cascade_filename(name: str) -> str:
        safe = re.sub(r"[^\w .-]", "_", str(name or "")).strip(" .") or "cascade"
        return safe + ".runtime.json"

    def _runtime_path(self) -> str:
        base = Path(self.settings.log_dir or "LOG").resolve().parent / "cascades" / ".runtime"
        base.mkdir(parents=True, exist_ok=True)
        return str(base / (canonical_sha256(self._runtime_identity()) + ".runtime.json"))

    def _runtime_identity(self) -> dict[str, Any]:
        return {
            "version": 1,
            "project": self.cfg.project,
            "in_dir": os.path.normcase(str(Path(self.cfg.in_dir).resolve())) if self.cfg.in_dir else "",
            "out_dir": os.path.normcase(str(Path(self.cfg.out_dir).resolve())) if self.cfg.out_dir else "",
            "name": self.cfg.cascade.name,
            "created_at": self.cfg.cascade.created_at,
        }

    def _read_runtime_state(self) -> dict[str, Any]:
        path = Path(self._runtime_path())
        if not path.is_file():
            legacy = path.parent / self._cascade_filename(self.cfg.cascade.name)
            if legacy.is_file():
                return self._read_legacy_runtime(legacy)
            return {}
        try:
            state = parse_json_strict(path.read_text(encoding="utf-8"))
            if state.get("runtime_identity") != self._runtime_identity():
                raise ContractError("Runtime kaskády patří jiné identitě.")
            return state
        except (OSError, ValueError, ContractError) as exc:
            raise ContractError("Kaskádový runtime stav obsahuje nekanonický JSON.") from exc

    @staticmethod
    def _evidence_identity(evidence):
        """Vlastníka staré evidence lze odvodit jen z úplných kanonických údajů."""
        definition = evidence.get("cascade_definition")
        if not isinstance(definition, dict):
            raise ContractError("Chybí kanonická definice vlastníka kaskády.")
        if any(not isinstance(evidence.get(key), str) for key in ("project", "in_dir", "out_dir")):
            raise ContractError("Chybí projekt nebo kořeny vlastníka kaskády.")
        if not isinstance(definition.get("name"), str) or type(definition.get("created_at")) not in (int, float):
            raise ContractError("Chybí stabilní identita definice kaskády.")
        roots = {}
        for key in ("in_dir", "out_dir"):
            value = evidence[key]
            if value and not Path(value).is_absolute():
                raise ContractError("Relativní historický kořen nedokládá vlastníka kaskády.")
            roots[key] = os.path.normcase(str(Path(value).resolve())) if value else ""
        return {
            "version": 1, "project": evidence["project"], **roots,
            "name": definition["name"], "created_at": definition["created_at"],
        }

    def _read_legacy_runtime(self, path):
        try:
            legacy = parse_json_strict(path.read_text(encoding="utf-8"))
            run_id = legacy.get("run_id")
            if not isinstance(run_id, str) or not run_id.strip():
                raise ContractError("Starý runtime neodkazuje na kanonický běh.")
            root = Path(safe_join_under_root(Path(self.settings.log_dir).resolve(), run_id))
            evidence = parse_json_strict((root / "run_state.json").read_text(encoding="utf-8"))
            identity = self._evidence_identity(evidence)
            recorded = evidence.get("runtime_identity")
            if recorded is not None and recorded != identity:
                raise ContractError("Kanonické údaje vlastníka kaskády si odporují.")
            if identity != self._runtime_identity():
                return {}
            return self._runtime_evidence({**legacy, "runtime_identity": identity})
        except (OSError, ValueError, ContractError) as exc:
            raise ContractError(
                "Starý runtime kaskády nemá jednoznačnou identitu nebo platnou evidenci; "
                "vyžaduje ověření původního běhu."
            ) from exc

    def _runtime_evidence(self, state):
        """Sdílený runtime je pouze ukazatel; rozhoduje kanonický stav běhu."""
        if not state.get("run_id"):
            return state
        root = Path(safe_join_under_root(Path(self.settings.log_dir).resolve(), state["run_id"]))
        evidence = parse_json_strict((root / "run_state.json").read_text(encoding="utf-8"))
        identity = evidence.get("runtime_identity")
        if identity is None:
            identity = self._evidence_identity(evidence)
        if identity != self._runtime_identity():
            raise ContractError("Zdrojový běh neodpovídá identitě kaskády.")
        if not isinstance(evidence.get("cascade_runtime"), dict):
            raise ContractError("Kanonický běh nemá platný mezistav kaskády; odvozenou cache nelze použít.")
        result = dict(state)
        result["cache"] = evidence.get("cascade_runtime")
        result["status"] = evidence.get("status")
        if state.get("status") == "submission_unknown":
            result["status"] = "submission_unknown"
        database = root.parent / "orchestration.sqlite3"
        if database.is_file():
            with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                uncertain = db.execute(
                    "SELECT 1 FROM provider_operations p JOIN work_orders w "
                    "ON w.work_order_hash=p.work_order_hash WHERE w.run_id=? "
                    "AND p.state='submission_unknown' LIMIT 1", (state["run_id"],),
                ).fetchone()
            if uncertain:
                result["status"] = "submission_unknown"
        return result

    def _write_runtime_state(self, patch: dict[str, Any]) -> None:
        state = self._read_runtime_state()
        state.update(patch)
        state["cascade_name"] = self.cfg.cascade.name
        state["runtime_identity"] = self._runtime_identity()
        state["updated_at"] = time.time()
        if self.logger and "cache" in patch:
            self.logger.update_state({
                "runtime_identity": self._runtime_identity(),
                "cascade_runtime": patch["cache"],
            })
        atomic_write_text(
            self._runtime_path(),
            canonical_bytes(state).decode("utf-8"),
        )

    def _resolve_text(self, text: str | None, context: dict[str, Any]) -> str:
        if not text:
            return ""

        def repl(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            key = match.group(2)
            rel_suffix = (match.group(3) or "").strip()
            if key in ("response_id", "json", "text"):
                storage_key = f"step.{idx}.{key}"
                if storage_key not in context or context.get(storage_key) in (None, ""):
                    raise ContractError(f"Chybí hodnota odkazu: {storage_key}")
                value = context[storage_key]
                if isinstance(value, str):
                    return value
                return json.dumps(value, ensure_ascii=False)
            if key in ("out_file_path", "out_file_id"):
                if not rel_suffix:
                    raise ContractError("Odkaz na výstupní soubor vyžaduje relativní cestu.")
                validate_relative_path(rel_suffix)
                storage_key = f"step.{idx}.{key}:{rel_suffix}"
                if not context.get(storage_key):
                    raise ContractError(f"Chybí hodnota odkazu: {storage_key}")
                return str(context[storage_key])
            return ""

        return PLACEHOLDER_RE.sub(repl, text)

    def _resolve_json(self, obj: Any, context: dict[str, Any]) -> Any:
        if isinstance(obj, str):
            return self._resolve_text(obj, context)
        if isinstance(obj, list):
            return [self._resolve_json(value, context) for value in obj]
        if isinstance(obj, dict):
            return {key: self._resolve_json(value, context) for key, value in obj.items()}
        return obj

    def _schema_for_step(self, step: CascadeStep) -> dict[str, Any] | None:
        if step.deterministic:
            if (any(output.kind == "file" and output.file_type in {"xlsx", "docx", "pdf", "pptx", "zip"} for output in step.outputs)
                    and "code_interpreter" not in model_spec(step.model).get("features", [])):
                raise ContractError(f"{step.model}: výroba dokumentů vyžaduje Code Interpreter.")
            return runtime_schema_for_step(step)
        if step.output_type != "json":
            return None
        if step.output_schema_kind == "manifest":
            return copy.deepcopy(PRESET_MANIFEST_SCHEMA)
        if step.output_schema_kind == "prompts":
            return copy.deepcopy(PRESET_PROMPTS_SCHEMA)
        if step.output_schema_kind == "custom" and isinstance(step.output_schema_custom, dict):
            return copy.deepcopy(step.output_schema_custom)
        return None

    def _validate_schema_minimal(self, schema: dict[str, Any]) -> None:
        if not isinstance(schema, dict):
            raise RuntimeError("Schema musí být JSON object.")
        if "type" not in schema and "properties" not in schema:
            raise RuntimeError("Schema musí obsahovat aspoň 'type' nebo 'properties'.")
        jsonschema.Draft202012Validator.check_schema(schema)

        def check_refs(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ("$ref", "$dynamicRef") and (
                        not isinstance(child, str) or not child.startswith("#")
                    ):
                        raise ValueError("JSON Schema smí odkazovat pouze uvnitř vlastního dokumentu.")
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)

        check_refs(schema)

    def _validate_json_output(self, obj: dict[str, Any], schema: dict[str, Any]) -> None:
        if schema:
            self._validate_schema_minimal(schema)
        jsonschema.validate(obj, schema)
        if not isinstance(obj, dict):
            raise RuntimeError("JSON výstup musí být objekt.")

    def _normalize_content_parts(self, resolved_content_json: Any, idx: int) -> list[dict[str, Any]]:
        if isinstance(resolved_content_json, list):
            out: list[dict[str, Any]] = []
            for part in resolved_content_json:
                if not isinstance(part, dict):
                    raise RuntimeError(
                        f"input_content_json list musí obsahovat object part (krok {idx})"
                    )
                out.append(part)
            return out
        if isinstance(resolved_content_json, dict):
            return [resolved_content_json]
        raise RuntimeError(f"input_content_json musí být object nebo list (krok {idx})")

    @staticmethod
    def _extract_input_file_ids(parts: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for part in parts:
            if isinstance(part, dict) and str(part.get("type") or "") == "input_file":
                fid = str(part.get("file_id") or "").strip()
                if fid:
                    ids.add(fid)
        return ids

    def _normalize_expected_rel_path(self, rel_path: str) -> str:
        return validate_relative_path(rel_path)

    def _select_out_dir_for_step(self) -> str:
        runtime_out = (self.cfg.out_dir or "").strip()
        if runtime_out:
            return runtime_out
        return (self.cfg.cascade.default_out_dir or "").strip()

    @staticmethod
    def _decode_file_content(row: dict[str, Any]) -> bytes:
        content = row.get("content")
        if not isinstance(content, str):
            raise ContractError("Obsah výstupního souboru musí být text nebo base64.")
        encoding = str(row.get("encoding") or "utf-8").lower()
        if encoding == "utf-8":
            return content.encode("utf-8")
        if encoding == "base64":
            try:
                return base64.b64decode(content, validate=True)
            except Exception as exc:
                raise ContractError("Výstupní soubor obsahuje neplatná base64 data.") from exc
        raise ContractError("Neznámé kódování výstupního souboru.")

    def _freeze_cascade_targets(
        self,
        step: CascadeStep,
        idx: int,
    ) -> None:
        out_dir = self._select_out_dir_for_step()
        paths = {
            self._normalize_expected_rel_path(path)
            for path in (step.expected_out_files or [])
            if str(path).strip()
        }
        paths.update(
            self._normalize_expected_rel_path(output.file_name)
            for output in step.outputs
            if output.kind == "file" and str(output.file_name).strip()
        )
        if not paths:
            return
        if not out_dir:
            raise RuntimeError(
                f"Krok {idx}: souborový výstup vyžaduje OUT adresář."
            )
        out_abs = os.path.abspath(out_dir)
        expected = getattr(self, "_cascade_expected_hashes", None)
        if expected is None:
            expected = {}
            self._cascade_expected_hashes = expected
        baseline = getattr(self, "_cascade_original_expected_hashes", None)
        if baseline is None:
            baseline = dict(expected)
            self._cascade_original_expected_hashes = baseline
        for rel in sorted(paths):
            if rel in expected:
                continue
            if rel in baseline:
                expected[rel] = baseline[rel]
                continue
            destination = safe_join_under_root(
                out_abs, rel.replace("/", os.sep)
            )
            expected[rel] = (
                sha256_file(destination)
                if os.path.isfile(destination)
                else None
            )
            baseline[rel] = expected[rel]
        if self.logger:
            self.logger.save_json(
                "manifests",
                f"cascade_step_{idx:02d}_target_expectations",
                {
                    "out_dir": out_abs,
                    "expected_target_hashes": {
                        rel: expected[rel] for rel in sorted(paths)
                    },
                },
                step_id=str(
                    getattr(self, "_current_step_record_id", "") or ""
                ),
            )

    def _write_files_atomically(
        self,
        rows: list[dict[str, Any]],
        out_dir: str,
        step_idx: int,
    ) -> dict[str, Any]:
        """Stage a complete file set; OUT is published only after all steps."""
        del out_dir
        if not self.logger:
            raise RuntimeError("Kaskádový staging vyžaduje RunLogger.")
        run_root = Path(self.logger.paths.run_dir).resolve()
        stage_root = (
            run_root / "staging" / "cascade"
            / f"step_{step_idx:04d}" / "generated"
        )
        ensure_dir(str(stage_root))

        normalized: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        for row in rows:
            rel = self._normalize_expected_rel_path(str(row.get("path") or ""))
            key = os.path.normcase(rel.replace("\\", "/"))
            if key in seen:
                raise ContractError(
                    f"Krok {step_idx}: duplicitní/case-collision cesta {rel}."
                )
            seen.add(key)
            if rel not in getattr(self, "_cascade_expected_hashes", {}):
                raise ContractError(
                    f"Krok {step_idx}: cesta {rel} nemá před výrobou "
                    "zmrazený očekávaný stav cíle."
                )
            normalized.append((rel, self._decode_file_content(row)))

        staged: list[dict[str, Any]] = []
        for rel, data in normalized:
            dst = Path(
                safe_join_under_root(
                    str(stage_root), rel.replace("/", os.sep)
                )
            )
            ensure_dir(str(dst.parent))
            fd, temp_path = tempfile.mkstemp(
                prefix=".cascade_stage_", dir=str(dst.parent)
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, dst)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(temp_path)
            digest = sha256_file(str(dst))
            row = {
                "path": rel,
                "staged_path": dst.relative_to(run_root).as_posix(),
                "sha256": digest,
                "bytes": dst.stat().st_size,
                "expected_target_hash": self._cascade_expected_hashes[rel],
                "cascade_step": step_idx,
            }
            staged.append(row)
            self.logger.bundle.archive_artifact(
                dst,
                role="staged_output",
                kind="output_file",
                step_id=str(
                    getattr(self, "_current_step_record_id", "") or ""
                ),
                reconstruction_role=rel,
                reusable=True,
                metadata={
                    "relative_path": rel,
                    "sha256": digest,
                    "expected_target_hash": self._cascade_expected_hashes[rel],
                    "publication": "not_published",
                    "cascade_step": step_idx,
                },
            )

        aggregate = getattr(self, "_cascade_staged_files", None)
        if aggregate is None:
            aggregate = {}
            self._cascade_staged_files = aggregate
        for row in staged:
            aggregate[row["path"]] = row
        self.logger.save_json(
            "manifests",
            f"cascade_step_{step_idx:02d}_staged_map",
            {
                "staged": staged,
                "publication": "deferred_until_cascade_complete",
            },
            step_id=str(getattr(self, "_current_step_record_id", "") or ""),
        )
        self.logger.update_state(
            {
                "cascade_staged_files": [
                    aggregate[path] for path in sorted(aggregate)
                ],
                "cascade_expected_target_hashes": dict(
                    self._cascade_expected_hashes
                ),
                "publication_state": "staged_not_published",
            }
        )
        return {"staged": staged, "out_dir": ""}

    def _publish_cascade_outputs(self) -> dict[str, Any] | None:
        logger = self.logger
        if logger is None:
            raise RuntimeError("Cascade logger není inicializovaný.")
        staged_map = getattr(self, "_cascade_staged_files", {}) or {}
        if not staged_map:
            return None
        out_dir = self._select_out_dir_for_step()
        if not out_dir:
            raise RuntimeError("Kaskádová publikace nemá OUT adresář.")
        staged = [staged_map[path] for path in sorted(staged_map)]
        expected = getattr(self, "_cascade_expected_hashes", {}) or {}
        if set(expected) != {row["path"] for row in staged}:
            missing = sorted({row["path"] for row in staged} - set(expected))
            unstaged = sorted(set(expected) - {row["path"] for row in staged})
            raise ContractError(
                "Kaskádová publikace: chybí původní očekávání pro "
                + repr(missing) + "; chybí staging pro " + repr(unstaged)
            )
        plan = prepare_publish(
            staged,
            out_dir,
            expected,
            run_dir=logger.paths.run_dir,
        )
        report = commit_publish(
            plan, run_dir=logger.paths.run_dir
        )
        logger.update_state(
            {
                "published_files": report["published"],
                "publish_report": report,
                "publication_state": "published",
            }
        )
        return report

    def _save_manifest_to_out(
        self,
        files: list[dict[str, Any]],
        out_dir: str,
        step_idx: int,
    ) -> dict[str, Any]:
        validate_paths(files)
        normalized = []
        for row in files:
            normalized.append(
                {
                    "path": str(row.get("path") or ""),
                    "content": row.get("content"),
                    "encoding": str(row.get("encoding") or "utf-8"),
                }
            )
        return self._write_files_atomically(normalized, out_dir, step_idx)

    def _process_expected_out_files(
        self,
        *,
        step: CascadeStep,
        idx: int,
        json_output: Any,
        context: dict[str, Any],
        client: OpenAIClient,
    ) -> dict[str, Any]:
        expected = [
            self._normalize_expected_rel_path(path)
            for path in (step.expected_out_files or [])
            if str(path).strip()
        ]
        if not expected:
            return {}
        out_dir = self._select_out_dir_for_step()
        if not out_dir:
            raise RuntimeError(
                f"Krok {idx}: výstupní soubory vyžadují OUT adresář."
            )
        if not isinstance(json_output, dict):
            raise RuntimeError(f"Krok {idx}: očekáván JSON object s manifestem souborů.")
        files = json_output.get("files")
        if not isinstance(files, list):
            raise RuntimeError(f"Krok {idx}: očekáván JSON manifest se seznamem 'files'.")

        normalized_manifest: list[dict[str, Any]] = []
        for row in files:
            if not isinstance(row, dict):
                raise RuntimeError(f"Krok {idx}: položka files[] musí být object.")
            rel = self._normalize_expected_rel_path(str(row.get("path") or ""))
            normalized_manifest.append(
                {
                    "path": rel,
                    "content": row.get("content"),
                    "encoding": str(row.get("encoding") or "utf-8"),
                }
            )
        manifest_paths = [row["path"] for row in normalized_manifest]
        if len(manifest_paths) != len({os.path.normcase(path) for path in manifest_paths}):
            raise RuntimeError(
                f"Krok {idx}: manifest obsahuje duplicitní/case-collision cestu."
            )
        missing = sorted(set(expected) - set(manifest_paths))
        unexpected = sorted(set(manifest_paths) - set(expected))
        if missing or unexpected:
            details = []
            if missing:
                details.append("chybí: " + ", ".join(missing))
            if unexpected:
                details.append("neočekávané: " + ", ".join(unexpected))
            raise RuntimeError(
                f"Krok {idx}: manifest neodpovídá schválené množině (" +
                "; ".join(details) + ")."
            )

        for row in normalized_manifest:
            self._decode_file_content(row)
        stage_report = self._save_manifest_to_out(
            normalized_manifest, out_dir, idx
        )
        staged_by_path = {
            row["path"]: row for row in stage_report["staged"]
        }

        logger = self.logger
        if logger is None:
            raise RuntimeError("Cascade logger není inicializovaný.")
        result: dict[str, dict[str, str]] = {}
        run_root = Path(logger.paths.run_dir).resolve()
        for rel in expected:
            staged_row = staged_by_path[rel]
            staged_path = (run_root / staged_row["staged_path"]).resolve()
            staged_path.relative_to(run_root)
            uploaded = client.upload_file(str(staged_path), purpose='user_data')
            file_id = str(uploaded.get("id") or "").strip()
            if not file_id:
                raise RuntimeError(f"Krok {idx}: upload souboru nevrátil file_id: {rel}")
            context[f"step.{idx}.out_file_path:{rel}"] = str(staged_path)
            context[f"step.{idx}.out_file_id:{rel}"] = file_id
            result[rel] = {"path": str(staged_path), "file_id": file_id}
        return result

    def _load_resume_cache(
        self,
        start_index: int,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any], set[str]]:
        if start_index <= 0 and self.cfg.resume_snapshot is None and not self.cfg.cascade.run_from_step_id:
            return {}, {}, {}, set()
        state = getattr(self, "_resume_runtime_state", None)
        if state is None:
            state = self._runtime_evidence(self._read_runtime_state())
        cache = self.cfg.resume_snapshot if self.cfg.resume_snapshot is not None else state.get("cache")
        if not isinstance(cache, dict):
            raise CascadeValidationError(
                "Pro spuštění od vybraného kroku chybí předchozí dokončený stav; spusťte kaskádu od začátku."
            )
        if cache.get("version", 1) not in {1, 2}:
            raise CascadeValidationError("Nepodporovaná verze cache kaskády.")
        self._primary_responses = copy.deepcopy(cache.get("primary_responses") or {})
        if not self.cfg.resume_source_dir and state.get("run_id"):
            self.cfg.resume_source_dir = str(Path(self.settings.log_dir) / state["run_id"])
        assert self.logger is not None
        self.logger._cascade_resume_root = self.cfg.resume_source_dir
        signatures = cache.get("step_signatures", {})
        if not isinstance(signatures, dict):
            signatures = {}
        context = cache.get("legacy_context", {})
        context_ids = cache.get("context_response_ids", {})
        values = cache.get("values", {})
        executed = set(cache.get("executed_step_ids", []))
        if not isinstance(context, dict) or not isinstance(context_ids, dict) or not isinstance(values, dict):
            raise CascadeValidationError(
                "Uložený mezistav kaskády je poškozený; spusťte kaskádu od začátku."
            )
        self._cascade_staged_files = copy.deepcopy(
            cache.get("cascade_staged_files") or {}
        )
        self._cascade_expected_hashes = copy.deepcopy(
            cache.get("cascade_expected_target_hashes") or {}
        )
        self._cascade_original_expected_hashes = copy.deepcopy(
            cache.get("cascade_original_expected_hashes") or self._cascade_expected_hashes
        )
        if self.cfg.resume_source_dir:
            parent = Path(self.cfg.resume_source_dir).resolve()
            child = Path(self.logger.paths.run_dir).resolve()
            replacements = {}
            for row in self._cascade_staged_files.values():
                source = Path(safe_join_under_root(parent, row["staged_path"]))
                if not source.is_file() or sha256_file(str(source)) != row["sha256"]:
                    raise ContractError(f"Neplatný zdroj stagingu kaskády: {row['path']}")
                artifact = self.logger.bundle.archive_artifact(
                    source, role="staged_output", kind="output_file", reusable=True,
                    reconstruction_role=row["path"], metadata={"source_run_id": parent.name},
                )
                row["staged_path"] = artifact["path_in_bundle"]
                replacements[str(source)] = str(child / row["staged_path"])
            def relocated(value):
                if isinstance(value, dict):
                    return {key: relocated(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [relocated(item) for item in value]
                return replacements.get(value, value) if isinstance(value, str) else value
            context, values = relocated(context), relocated(values)
        for index in range(start_index):
            step = self.cfg.cascade.steps[index]
            if step.id in executed:
                self._archive_dynamic_inputs(step, context)
            if cache.get("version") == 2:
                expected_signature = self._semantic_step_signature(step)
            else:
                legacy = step.to_dict()
                if legacy.get("previous_response_id_expr"):
                    raise CascadeValidationError("Cache V1 nedokládá explicitní kontext; spusťte tento krok znovu.")
                legacy.pop("previous_response_id_expr", None)
                expected_signature = canonical_sha256(legacy)
            if signatures.get(step.id) != expected_signature:
                raise CascadeValidationError(
                    f"Předchozí krok {index + 1} se od posledního běhu změnil; spusťte kaskádu nejpozději od tohoto kroku."
                )
        return (
            copy.deepcopy(context),
            {str(k): str(v) for k, v in context_ids.items() if v},
            copy.deepcopy(values),
            executed,
        )

    @staticmethod
    def _value_key(step_id: str, output_id: str) -> str:
        return f"{step_id}|{output_id}"

    def _resolve_deterministic_inputs(
        self,
        *,
        step: CascadeStep,
        idx: int,
        values: dict[str, Any],
        client: OpenAIClient,
    ) -> tuple[str, list[str], list[dict[str, Any]]]:
        extra_text: list[str] = []
        file_ids: list[str] = []
        bindings: list[dict[str, Any]] = []

        def bind(item, file_id, filename):
            from .compat import SUPPORTED_INPUT_IMAGE_EXTS
            representation = "input_image" if Path(filename).suffix.lower() in SUPPORTED_INPUT_IMAGE_EXTS else "input_file"
            file_ids.append(file_id)
            bindings.append({"input_id": item.id, "name": item.name, "file_id": file_id,
                             "filename": filename, "type": representation})

        for item in step.inputs:
            if item.source == "text":
                extra_text.append(f"[Vstup {item.id}: {item.name}]\n{item.value}")
                continue
            if item.source == "local_file":
                path = self._frozen_input_path(step.id, "input", item.id)
                if not os.path.isfile(path):
                    raise RuntimeError(f"Vstupní soubor neexistuje: {path}")
                uploaded = client.upload_file(path, purpose='user_data')
                file_id = str(uploaded.get("id") or "").strip()
                if not file_id:
                    raise RuntimeError(f"Upload souboru nevrátil file_id: {path}")
                bind(item, file_id, Path(path).name)
                continue
            if item.source == "file_id":
                if item.value.strip():
                    file_id = item.value.strip()
                    metadata = client.retrieve_file(file_id)
                    if not isinstance(metadata, dict) or not isinstance(metadata.get("filename"), str):
                        raise ContractError(f"Vstup {item.id} nemá doložený typ souboru.")
                    bind(item, file_id, metadata["filename"])
                continue
            if item.source == "output":
                key = self._value_key(item.source_step_id, item.source_output_id)
                if key not in values:
                    raise ContractError(
                        f"Krok {idx}: vstup „{item.name}“ nemá k dispozici výstup předchozího kroku."
                    )
                value = values[key]
                if isinstance(value, dict) and value.get("kind") == "file":
                    file_id = str(value.get("file_id") or "").strip()
                    if file_id:
                        bind(item, file_id, str(value.get("path") or "output." + str(value.get("file_type") or "")))
                    elif value.get("path") and os.path.isfile(str(value["path"])):
                        uploaded = client.upload_file(str(value['path']), purpose='user_data')
                        file_id = str(uploaded.get("id") or "").strip()
                        if not file_id:
                            raise RuntimeError("Upload návazného souboru nevrátil file_id.")
                        bind(item, file_id, str(value["path"]))
                    else:
                        raise RuntimeError("Návazný soubor už není dostupný.")
                else:
                    visible = value.get("value") if isinstance(value, dict) and "value" in value else value
                    if not isinstance(visible, str):
                        visible = json.dumps(visible, ensure_ascii=False)
                    extra_text.append(f"[Vstup {item.id}: {item.name}]\n{visible}")
        text = step.input_text
        if extra_text:
            text = text.rstrip() + "\n\n" + "\n\n".join(extra_text)
        if bindings:
            text += "\n\nMapování příloh na vstupy: " + json.dumps(bindings, ensure_ascii=False)
        return text, file_ids, bindings

    def _deterministic_instructions(self, step: CascadeStep) -> str:
        lines = [
            "Dodrž přesně výstupní kontrakt tohoto kroku.",
            "Nevracej žádné další klíče ani doprovodný text.",
        ]
        for output in step.outputs:
            lines.append("- " + describe_output(output))
            if output.kind == "json":
                lines.append(
                    f"  Hodnotu „{output.name}“ vrať jako JSON objekt odpovídající přesně jeho vnořené JSON Schema masce."
                )
            if output.kind == "file" and output.file_mode == "modify":
                lines.append(
                    f"  Soubor „{output.name}“ uprav výhradně z input_id={output.modify_input_id}; zachovej chování mimo zadanou změnu."
                )
        prefix = step.instructions.strip()
        return (prefix + "\n\n" if prefix else "") + "\n".join(lines)

    def _prepare_step(
        self,
        *,
        step: CascadeStep,
        idx: int,
        context: dict[str, Any],
        context_response_ids: dict[str, str],
        values: dict[str, Any],
        client: OpenAIClient,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        if step.deterministic:
            resolved_input_text, file_ids, bindings = self._resolve_deterministic_inputs(
                step=step,
                idx=idx,
                values=values,
                client=client,
            )
            if self.cfg.recovery_instruction:
                resolved_input_text += self._recovery_suffix()
            content_parts: list[dict[str, Any]] = [
                {"type": "input_text", "text": resolved_input_text}
            ]
            existing_file_ids = set()
            for binding in bindings:
                file_id = binding["file_id"]
                if file_id and file_id not in existing_file_ids:
                    content_parts.append({"type": binding["type"], "file_id": file_id})
                    existing_file_ids.add(file_id)
            self._step_input_bindings = bindings
            schema = self._schema_for_step(step)
            spec = model_spec(step.model)
            output_limit = spec.get("max_output_tokens")
            if type(output_limit) is not int or output_limit <= 0:
                raise ContractError(
                    f"Krok {idx}: pro model {step.model} chybí doložený max_output_tokens."
                )
            payload: dict[str, Any] = {
                "model": step.model,
                "instructions": self._deterministic_instructions(step),
                "input": [{"type": "message", "role": "user", "content": content_parts}],
                "text": response_format(f"cascade_step_{idx:02d}_det", schema),
                "max_output_tokens": output_limit,
                "truncation": "disabled",
            }
            previous_id = (
                context_response_ids.get(step.context_id, "")
                if step.use_conversation_context
                else ""
            )
            if step.use_conversation_context and not previous_id:
                raise ContractError(
                    f"Krok {idx}: konverzační dependency {step.context_id!r} nemá "
                    "doložené previous_response_id z předchozího kroku."
                )
            if previous_id:
                payload["previous_response_id"] = previous_id
            if step.temperature is not None:
                payload["temperature"] = float(step.temperature)
            validate_response_payload(payload)
            client.validate_prepared_payload(payload)
            return payload, schema or {}, file_ids

        # Legacy compatibility path.
        legacy_file_ids: list[str] = []
        for expression in step.files_existing_ids or []:
            resolved = self._resolve_text(expression, context).strip()
            if resolved:
                legacy_file_ids.append(resolved)
        resolved_input = self._resolve_text(step.input_text, context)
        if self.cfg.recovery_instruction:
            resolved_input += self._recovery_suffix()
        resolved_instructions = self._resolve_text(step.instructions, context)
        resolved_content = (
            self._resolve_json(step.input_content_json, context)
            if step.input_content_json is not None
            else None
        )
        content_parts = (
            self._normalize_content_parts(resolved_content, idx)
            if resolved_content is not None
            else [{"type": "input_text", "text": resolved_input}]
        )
        if self.cfg.recovery_instruction and resolved_content is not None:
            content_parts.append({"type": "input_text", "text": self._recovery_suffix().strip()})

        # Validate the wire shape before any local file is uploaded, so a bad
        # content part cannot create chargeable/orphaned uploads.
        preflight_parts = copy.deepcopy(content_parts)
        preflight_existing = self._extract_input_file_ids(preflight_parts)
        for file_id in legacy_file_ids:
            if file_id and file_id not in preflight_existing:
                preflight_parts.append({"type": "input_file", "file_id": file_id})
                preflight_existing.add(file_id)
        if step.files_local_paths and "file_local_validation" not in preflight_existing:
            preflight_parts.append({"type": "input_file", "file_id": "file_local_validation"})
        spec = model_spec(step.model)
        output_limit = spec.get("max_output_tokens")
        if type(output_limit) is not int or output_limit <= 0:
            raise ContractError(
                f"Krok {idx}: pro model {step.model} chybí doložený max_output_tokens."
            )
        preflight_payload = {
            "model": step.model,
            "instructions": resolved_instructions,
            "input": [{"type": "message", "role": "user", "content": preflight_parts}],
            "text": text_format(),
            "max_output_tokens": output_limit,
            "truncation": "disabled",
        }
        if step.temperature is not None:
            preflight_payload["temperature"] = float(step.temperature)
        validate_response_payload(preflight_payload)
        client.validate_prepared_payload(preflight_payload)

        for index, _local_path in enumerate(step.files_local_paths or []):
            resolved_path = self._frozen_input_path(step.id, "files_local_paths", str(index))
            if not os.path.isfile(resolved_path):
                raise RuntimeError(f"Lokální soubor neexistuje: {resolved_path}")
            uploaded = client.upload_file(resolved_path, purpose='user_data')
            file_id = str(uploaded.get("id") or "").strip()
            if not file_id:
                raise RuntimeError(f"Upload souboru nevrátil file_id: {resolved_path}")
            legacy_file_ids.append(file_id)

        existing = self._extract_input_file_ids(content_parts)
        for file_id in legacy_file_ids:
            if file_id and file_id not in existing:
                content_parts.append({"type": "input_file", "file_id": file_id})
                existing.add(file_id)

        schema = self._schema_for_step(step)
        if step.output_type == "json":
            if schema is None:
                schema = resolve_schema(
                    client,
                    step.model,
                    step.instructions + "\n" + step.input_text,
                    None,
                    [],
                    request=lambda payload, attempt: self._schema_request(
                        client, step, idx, payload, attempt
                    ),
                    max_output_tokens=output_limit,
                )
            wire_format = response_format(f"cascade_step_{idx:02d}_schema", schema)
        else:
            wire_format = text_format()

        payload = {
            "model": step.model,
            "instructions": resolved_instructions,
            "input": [{"type": "message", "role": "user", "content": content_parts}],
            "text": wire_format,
            "max_output_tokens": output_limit,
            "truncation": "disabled",
        }
        if step.temperature is not None:
            payload["temperature"] = float(step.temperature)
        resolved_prev = self._resolve_text(step.previous_response_id_expr or "", context).strip()
        if resolved_prev:
            payload["previous_response_id"] = resolved_prev
        validate_response_payload(payload)
        client.validate_prepared_payload(payload)
        return payload, schema or {}, legacy_file_ids

    def _process_deterministic_output(
        self,
        *,
        step: CascadeStep,
        idx: int,
        decoded: dict[str, Any],
        client: OpenAIClient,
        context: dict[str, Any],
        values: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        out_dir = self._select_out_dir_for_step()
        file_rows: list[tuple[CascadeOutput, dict[str, Any]]] = []
        decision_value: str | None = None
        summary: dict[str, Any] = {}

        for output in step.outputs:
            key = output_machine_key(output)
            if key not in decoded:
                raise ContractError(f"Krok {idx}: chybí deterministický výstup „{output.name}“.")
            raw = decoded[key]
            value_key = self._value_key(step.id, output.id)
            if output.kind == "text":
                if not isinstance(raw, str):
                    raise ContractError(f"Krok {idx}: výstup „{output.name}“ musí být text.")
                values[value_key] = {"kind": "text", "value": raw}
                summary[output.id] = raw
            elif output.kind == "json":
                if not isinstance(raw, dict):
                    raise ContractError(
                        f"Krok {idx}: strukturovaný výstup „{output.name}“ musí být JSON objekt."
                    )
                if not isinstance(output.json_schema, dict):
                    raise ContractError(
                        f"Krok {idx}: strukturovaný výstup „{output.name}“ nemá zmrazenou JSON masku."
                    )
                try:
                    jsonschema.Draft202012Validator(
                        output.json_schema,
                        format_checker=jsonschema.FormatChecker(),
                    ).validate(raw)
                except jsonschema.ValidationError as exc:
                    raise ContractError(
                        f"Krok {idx}: výstup „{output.name}“ porušuje svoji JSON masku: {exc.message}"
                    ) from exc
                parsed = copy.deepcopy(raw)
                values[value_key] = {"kind": "json", "value": parsed}
                summary[output.id] = parsed
            elif output.kind == "decision":
                if not isinstance(raw, str):
                    raise ContractError(f"Krok {idx}: rozhodnutí musí být text.")
                allowed = {item.value for item in output.decision_options}
                if raw not in allowed:
                    raise ContractError(
                        f"Krok {idx}: rozhodnutí „{raw}“ není mezi povolenými odpověďmi."
                    )
                values[value_key] = {"kind": "decision", "value": raw}
                summary[output.id] = raw
                decision_value = raw
            elif output.kind == "file":
                if not isinstance(raw, dict):
                    raise ContractError(f"Krok {idx}: souborový výstup musí být objekt.")
                if str(raw.get("path") or "") != output.file_name:
                    raise ContractError(
                        f"Krok {idx}: model vrátil jiný název souboru než „{output.file_name}“."
                    )
                self._decode_file_content(raw)
                file_rows.append((output, raw))
            else:
                raise ContractError(f"Krok {idx}: neznámý typ výstupu.")

        # Validate every file first, then write all.
        if file_rows:
            if not out_dir:
                raise RuntimeError("Souborový výstup vyžaduje OUT adresář.")
            rows = [row for _, row in file_rows]
            stage_report = self._write_files_atomically(rows, out_dir, idx)
            staged_by_path = {
                row["path"]: row for row in stage_report["staged"]
            }
            logger = self.logger
            if logger is None:
                raise RuntimeError("Cascade logger není inicializovaný.")
            run_root = Path(logger.paths.run_dir).resolve()
            for output, _row in file_rows:
                rel = output.file_name
                staged_row = staged_by_path[rel]
                path = (run_root / staged_row["staged_path"]).resolve()
                path.relative_to(run_root)
                uploaded = client.upload_file(str(path), purpose='user_data')
                file_id = str(uploaded.get("id") or "").strip()
                if not file_id:
                    raise RuntimeError(f"Krok {idx}: upload výstupu nevrátil file_id: {rel}")
                value = {"kind": "file", "path": str(path), "file_id": file_id, "file_type": output.file_type}
                values[self._value_key(step.id, output.id)] = value
                summary[output.id] = value
                context[f"step.{idx}.out_file_path:{rel}"] = str(path)
                context[f"step.{idx}.out_file_id:{rel}"] = file_id
        return summary, decision_value

    def _next_index_for_step(
        self,
        current_index: int,
        step: CascadeStep,
        decision_value: str | None,
    ) -> int:
        decisions = [output for output in step.outputs if output.kind == "decision"]
        if not decisions:
            return current_index + 1
        if decision_value is None:
            raise ContractError("Rozhodovací krok nevrátil volbu.")
        decision = decisions[0]
        option = next(
            (item for item in decision.decision_options if item.value == decision_value),
            None,
        )
        if option is None or not option.target_step_id:
            raise ContractError("Rozhodnutí nemá platně definovaný cílový krok.")
        target = self.cfg.cascade.step_index(option.target_step_id)
        if target <= current_index:
            raise ContractError("Rozhodnutí nesmí vytvořit smyčku ani návrat zpět.")
        return target

    def _cache_snapshot(
        self,
        *,
        context: dict[str, Any],
        context_response_ids: dict[str, str],
        values: dict[str, Any],
        executed_step_ids: set[str],
    ) -> dict[str, Any]:
        return {
            "version": 2,
            "primary_responses": copy.deepcopy(getattr(self, "_primary_responses", {})),
            "legacy_context": copy.deepcopy(context),
            "cascade_staged_files": copy.deepcopy(
                getattr(self, "_cascade_staged_files", {}) or {}
            ),
            "cascade_expected_target_hashes": copy.deepcopy(
                getattr(self, "_cascade_expected_hashes", {}) or {}
            ),
            "cascade_original_expected_hashes": copy.deepcopy(
                getattr(self, "_cascade_original_expected_hashes", {})
                or getattr(self, "_cascade_expected_hashes", {}) or {}
            ),
            "context_response_ids": copy.deepcopy(context_response_ids),
            "values": copy.deepcopy(values),
            "executed_step_ids": sorted(executed_step_ids),
            "step_signatures": {
                step.id: self._semantic_step_signature(step) for step in self.cfg.cascade.steps
            },
        }

    def _semantic_step_signature(self, step):
        return canonical_sha256({
            "version": 2, "definition": step_signature(step),
            "inputs": sorted([row["field"], row["input_id"], row["sha256"]]
                             for row in getattr(self, "_frozen_cascade_inputs", [])
                             if row["step_id"] == step.id),
        })

    def _selected_final_outputs(self, values: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for ref in self.cfg.cascade.final_outputs:
            key = self._value_key(ref.step_id, ref.output_id)
            if key in values:
                result[key] = copy.deepcopy(values[key])
        return result

    def execute(self) -> None:
        try:
            with ExecutionLock(self._runtime_path() + ".lock"):
                self._execute()
        except Exception as exc:
            self._emit_boundary_error(exc)

    def _emit_boundary_error(self, exc: Exception) -> None:
        from .user_errors import describe_error
        self.failure_detail.emit(describe_error(exc))
        self.progress_event.emit(ProgressEvent("RUN", "failed", detail=str(exc)))
        self.finished_err.emit(humanize_cascade_error(exc))

    def _execute(self) -> None:
        previous = self._runtime_evidence(self._read_runtime_state())
        if previous and not Path(self._runtime_path()).is_file():
            # Převezme pouze ověřený ukazatel; historický soubor zůstává beze změny.
            atomic_write_text(self._runtime_path(), canonical_bytes(previous).decode("utf-8"))
        self._resume_runtime_state = previous
        if previous.get("status") == "submission_unknown":
            message = "Předchozí odeslání nemá potvrzený výsledek; automatické opakování je zakázáno."
            from .user_errors import describe_error
            self.failure_detail.emit(describe_error(ContractError(message)))
            self.progress_event.emit(ProgressEvent("RUN", "submission_unknown", detail=message))
            self.finished_err.emit(message)
            return
        run_id = self.cfg.run_id or new_run_id()
        current_index = 0
        current_step_record_id = ""
        context: dict[str, Any] = {}
        context_response_ids: dict[str, str] = {}
        values: dict[str, Any] = {}
        executed_step_ids: set[str] = set()
        try:
            validate_cascade_definition(self.cfg.cascade, strict=True)
            progress_keys = [f"Krok {i}: {step.title or 'Bez názvu'}"
                             for i, step in enumerate(self.cfg.cascade.steps, 1)]
            self.progress_event.emit(ProgressEvent("PLAN", planned_steps=tuple([
                "Příprava posloupnosti", *progress_keys, "Uložení výsledků posloupnosti",
            ])))
            self.progress_event.emit(ProgressEvent("Příprava posloupnosti"))
            self.logger = CascadeLogger(
                self.settings.log_dir,
                run_id,
                project_name=self.cfg.project,
            )
            recovered_publish = recover_publish_journal(
                self.logger.paths.run_dir
            )
            if (
                recovered_publish
                and recovered_publish.get("status") == "committed"
                and previous.get("status") in {"running", "completed"}
            ):
                result = {
                    "mode": "KASKADA",
                    "run_id": run_id,
                    "status": "completed",
                    "publication_recovered": True,
                    "publish_report": recovered_publish,
                    "published_files": recovered_publish.get("published", []),
                }
                self.logger.update_state(
                    {
                        "status": "completed",
                        "result": result,
                        "publish_report": recovered_publish,
                        "published_files": recovered_publish.get("published", []),
                    }
                )
                self.finished_ok.emit(result)
                return
            if self.cfg.lineage:
                lineage = dict(self.cfg.lineage)
                source_run_id = str(lineage.pop("source_run_id", "") or "")
                relation_type = str(lineage.pop("relation_type", "") or "")
                if source_run_id and relation_type:
                    self.logger.record_lineage(source_run_id, relation_type, **lineage)
            input_artifacts, missing_input_artifacts = self._archive_cascade_inputs()
            self._cascade_input_artifact_ids = [row["artifact_id"] for row in input_artifacts]
            self._cascade_input_missing = list(missing_input_artifacts)
            approval_id = (
                self.cfg.execution_approval_id
                or f"user-start:{run_id}"
            )
            self.cfg.execution_approval_id = approval_id
            self.cfg.run_id = run_id
            self._register_operation_run(
                run_id, approval_id, input_artifacts
            )
            start_index = 0
            if self.cfg.cascade.run_from_step_id:
                start_index = self.cfg.cascade.step_index(self.cfg.cascade.run_from_step_id)
                if start_index < 0:
                    raise CascadeValidationError("Vybraný počáteční krok už neexistuje.")
            self._cascade_staged_files = {}
            self._cascade_expected_hashes = {}
            context, context_response_ids, values, executed_step_ids = self._load_resume_cache(
                start_index
            )
            # All values and response lineage from the selected step onward become invalid.
            invalid_step_ids = {
                step.id for step in self.cfg.cascade.steps[start_index:]
            }
            self._cascade_staged_files = {
                path: row for path, row in self._cascade_staged_files.items()
                if int(row.get("cascade_step") or 0) <= start_index
            }
            self._cascade_expected_hashes = {
                path: value for path, value in self._cascade_expected_hashes.items()
                if path in self._cascade_staged_files
            }
            values = {
                key: value
                for key, value in values.items()
                if key.split("|", 1)[0] not in invalid_step_ids
            }
            executed_step_ids = {
                step_id for step_id in executed_step_ids if step_id not in invalid_step_ids
            }
            context = {
                key: value
                for key, value in context.items()
                if not (
                    key.startswith("step.")
                    and key.split(".", 2)[1].isdigit()
                    and int(key.split(".", 2)[1]) >= start_index + 1
                )
            }
            if start_index > 0:
                context_response_ids = {}
                for prior_index in range(start_index):
                    prior_step = self.cfg.cascade.steps[prior_index]
                    response_id = str(
                        context.get(f"step.{prior_index + 1}.response_id") or ""
                    ).strip()
                    if response_id and prior_step.deterministic:
                        context_response_ids[prior_step.context_id] = response_id
            else:
                context_response_ids = {}
            current_index = start_index

            self.logger.update_state(
                {
                    "status": "running",
                    "started_at": time.time(),
                    "mode": "KASKADA",
                    "project": self.cfg.project,
                    "out_dir": self.cfg.out_dir,
                    "in_dir": self.cfg.in_dir,
                    "cascade_name": self.cfg.cascade.name,
                    "steps": len(self.cfg.cascade.steps),
                    "runtime_identity": self._runtime_identity(),
                    "start_step": start_index + 1,
                    "cascade_definition": self.cfg.cascade.to_dict(),
                    "cascade_runtime": self._cache_snapshot(
                        context=context,
                        context_response_ids=context_response_ids,
                        values=values,
                        executed_step_ids=executed_step_ids,
                    ),
                    "next_step_id": self.cfg.cascade.steps[start_index].id if start_index < len(self.cfg.cascade.steps) else "",
                    "recovery_instruction": self.cfg.recovery_instruction,
                    "cascade_input_artifacts": input_artifacts,
                    "cascade_input_missing": missing_input_artifacts,
                }
            )
            initial_state = parse_json_strict(Path(self.logger.state_path).read_text(encoding="utf-8"))
            self.logger.checkpoint(
                "cascade_input_ready",
                state_snapshot=initial_state,
                safe_to_continue=not missing_input_artifacts,
                reason=("Definice kaskády a rekonstruovatelné vstupy před prvním novým krokem jsou uloženy."
                        if not missing_input_artifacts else
                        "Některé lokální vstupy nelze archivovat; checkpoint není bezpečný pro pokračování."),
                required_artifact_ids=self._cascade_input_artifact_ids,
                invalidation_rules=[
                    "Změna signatury kteréhokoli zděděného kroku invaliduje checkpoint.",
                    "Chybějící lokální výstup nebo response evidence blokuje přímé pokračování.",
                ],
            )
            self._write_runtime_state(
                {
                    "status": "running",
                    "run_id": run_id,
                    "failed_step_id": "",
                    "failed_step_number": 0,
                    "human_error": "",
                    "technical_error": "",
                    "cache": self._cache_snapshot(
                        context=context,
                        context_response_ids=context_response_ids,
                        values=values,
                        executed_step_ids=executed_step_ids,
                    ),
                }
            )

            client = OpenAIClient(self.api_key, timeout_s=self.settings.response_timeout_s)
            client.evidence_bundle = self.logger.bundle
            client.configure_validation(self.settings)
            client.stopped = lambda: self._stop

            total = max(1, len(self.cfg.cascade.steps))
            per_step_response_ids: dict[str, str] = {}
            per_step_text: dict[str, str] = {}
            per_step_json: dict[str, Any] = {}
            per_step_out_files: dict[str, dict[str, Any]] = {}
            last_response_id = ""

            self.progress_event.emit(ProgressEvent("Příprava posloupnosti", "completed"))

            while current_index < len(self.cfg.cascade.steps):
                self._check_stop()
                self._failed_step_index = current_index
                idx = current_index + 1
                step = copy.deepcopy(self.cfg.cascade.steps[current_index])
                step_label = step.title or f"Krok {idx}"
                step_record = self.logger.bundle.ensure_step(
                    step.id,
                    title=step_label,
                    kind="cascade",
                    model=step.model,
                )
                current_step_record_id = str(step_record["step_id"])
                self._current_step_record_id = current_step_record_id
                base_p = int(current_index * 100 / total)
                self._emit_status(base_p, 0, f"Krok {idx}/{total}: {step_label}")
                self.progress_event.emit(
                    ProgressEvent(
                        progress_keys[current_index],
                        completed=len(executed_step_ids),
                        total=total,
                        unit="kroků",
                        detail=step_label,
                    )
                )

                self._check_stop()
                self._freeze_cascade_targets(step, idx)
                self._archive_dynamic_inputs(step, context)
                step_summary: dict[str, Any] = {}
                decision_value: str | None = None
                def semantic_value(value):
                    if isinstance(value, dict):
                        if value.get("kind") == "file":
                            path = value.get("path")
                            return {"kind": "file", "sha256": sha256_file(path), "file_type": value.get("file_type")}
                        return {key: semantic_value(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [semantic_value(item) for item in value]
                    return value
                primary_key = canonical_sha256({
                    "step": self._semantic_step_signature(step), "values": semantic_value(values),
                    "context_ids": context_response_ids if step.deterministic else {},
                    "instruction": self.cfg.recovery_instruction,
                    "legacy_context": {
                        key: {"sha256": sha256_file(value)} if ".out_file_path:" in key else value
                        for key, value in context.items()
                    } if not step.deterministic else {},
                })
                cached_primary = getattr(self, "_primary_responses", {}).get(primary_key)
                self.logger._cascade_resume_root = self.cfg.resume_source_dir if cached_primary else ""
                if cached_primary:
                    payload, schema = cached_primary["payload"], cached_primary["schema"]
                    response = cached_primary["response"]
                    decoded = validate_output(response, payload)
                    self._step_input_bindings = cached_primary["bindings"]
                    file_ids = cached_primary["file_ids"]
                    self.logger.save_json("responses", f"cascade_step_{idx:02d}_reused", response, step_id=current_step_record_id)
                else:
                    payload, schema, file_ids = self._prepare_step(
                        step=step, idx=idx, context=context, context_response_ids=context_response_ids,
                        values=values, client=client,
                    )
                self._emit_status(
                    base_p,
                    50,
                    f"Krok {idx}: požadavek na OpenAI",
                )
                original_instructions = str(payload.get("instructions") or "")
                for repair_attempt in range(0 if cached_primary else 3):
                    self._check_stop()
                    self.logger.save_json(
                        "requests", f"cascade_step_{idx:02d}_attempt_{repair_attempt + 1}",
                        payload, step_id=current_step_record_id,
                    )
                    operation_cfg = self._operation_cfg(
                        step.model,
                        self.cfg.execution_approval_id,
                    )
                    projection = {
                        "cascade_name": self.cfg.cascade.name,
                        "step_id": step.id,
                        "step_signature": step_signature(step),
                        "attempt_no": repair_attempt + 1,
                        "input": payload.get("input"),
                        "previous_response_id": payload.get(
                            "previous_response_id"
                        ),
                    }
                    wire_format = (payload.get("text") or {}).get(
                        "format"
                    ) or {}
                    order = freeze_order(
                        operation_cfg,
                        {
                            "run_id": run_id,
                            "step_id": current_step_record_id,
                            "task_id": step.id,
                            "stage": "CASCADE",
                            "route": "responses_live",
                            "provider_endpoint": "/v1/responses",
                            "target_id": step.id,
                            "target_path": None,
                            "expected_target_hash": None,
                            "contract_name": str(
                                wire_format.get("name")
                                or "CASCADE_TEXT"
                            ),
                            "schema": wire_format.get("schema") or {},
                            "request_payload": payload,
                            "prompt": str(
                                payload.get("instructions") or ""
                            ),
                            "model": step.model,
                            "model_capability": {},
                            "source_snapshot": {
                                "step_signature": step_signature(step),
                                "cascade_name": self.cfg.cascade.name,
                            },
                            "attempt_no": repair_attempt + 1,
                            "approval_id": (
                                self.cfg.execution_approval_id
                            ),
                        },
                        projection,
                    )
                    prepare_provider_request(
                        self.logger,
                        operation_cfg,
                        client,
                        payload,
                        work_order=order,
                    )
                    mark_submission_started(self.logger, order)
                    try:
                        response = client.create_response(payload)
                    except OutputContractError as exc:
                        response = getattr(exc, "response", None)
                        if not isinstance(response, dict):
                            raise
                    except Exception as exc:
                        if isinstance(
                            exc, SubmissionOutcomeUnknown
                        ):
                            mark_submission(
                                self.logger,
                                order,
                                None,
                                unknown=True,
                            )
                            self.logger.update_state(
                                {"status": "submission_unknown"}
                            )
                            self._write_runtime_state(
                                {
                                    "status": "submission_unknown",
                                    "run_id": run_id,
                                    "failed_step_id": step.id,
                                    "failed_step_number": idx,
                                }
                            )
                        elif (
                            getattr(exc, "request_sent", None)
                            is False
                            or getattr(exc, "status_code", None)
                            in {400, 401, 403, 404, 422, 429}
                        ):
                            mark_not_submitted(
                                self.logger, order
                            )
                        else:
                            mark_submission(
                                self.logger,
                                order,
                                None,
                                unknown=True,
                            )
                            self.logger.update_state(
                                {"status": "submission_unknown"}
                            )
                            self._write_runtime_state(
                                {
                                    "status": "submission_unknown",
                                    "run_id": run_id,
                                    "failed_step_id": step.id,
                                    "failed_step_number": idx,
                                }
                            )
                        raise
                    provider_id = str(
                        response.get("id") or ""
                    ).strip()
                    if not provider_id:
                        mark_submission(
                            self.logger,
                            order,
                            None,
                            unknown=True,
                        )
                        self.logger.update_state(
                            {"status": "submission_unknown"}
                        )
                        self._write_runtime_state(
                            {
                                "status": "submission_unknown",
                                "run_id": run_id,
                                "failed_step_id": step.id,
                                "failed_step_number": idx,
                            }
                        )
                        raise SubmissionOutcomeUnknown(
                            "cascade_response",
                            "POST",
                            "/v1/responses",
                        )
                    mark_submission(
                        self.logger,
                        order,
                        provider_id,
                        unknown=False,
                    )
                    record_usage(
                        self.logger, order, response
                    )
                    self.logger.save_json(
                        "responses", f"cascade_step_{idx:02d}_attempt_{repair_attempt + 1}",
                        response, step_id=current_step_record_id,
                    )
                    try:
                        decoded = validate_output(response, payload)
                        break
                    except OutputContractError as exc:
                        # Oprava prokazatelně přijatého výstupu není síťový retry.
                        # Příprava příloh ani následné delivery se nikdy neopakují.
                        if exc.response.get("status") != "completed" or exc.response.get("error"):
                            raise
                        self.logger.save_json(
                            "responses", f"cascade_step_{idx:02d}_invalid_{repair_attempt + 1}",
                            exc.response, step_id=current_step_record_id,
                        )
                        self.logger.event("cascade.output.invalid", {
                            "idx": idx, "attempt": repair_attempt + 1,
                            "response_id": exc.response.get("id"), "error": str(exc),
                        })
                        if repair_attempt == 2:
                            raise
                        payload = copy.deepcopy(payload)
                        payload.setdefault("metadata", {})["kajovo_repair_attempt"] = str(repair_attempt + 1)
                        payload["instructions"] = original_instructions + "\nOprav předchozí neplatný výstup: " + str(exc)

                self._primary_responses = {**getattr(self, "_primary_responses", {}), primary_key: {
                    "payload": copy.deepcopy(payload), "schema": schema, "response": response,
                    "bindings": copy.deepcopy(getattr(self, "_step_input_bindings", [])), "file_ids": file_ids,
                }}
                self.logger.update_state({"cascade_runtime": self._cache_snapshot(
                    context=context, context_response_ids=context_response_ids, values=values,
                    executed_step_ids=executed_step_ids,
                )})
                if step.deterministic:
                    self._validate_json_output(decoded, schema)
                    from .cascade_production import produce_binary_outputs
                    decoded = produce_binary_outputs(self, client, step, idx, payload, decoded)
                    step_summary, decision_value = self._process_deterministic_output(
                        step=step,
                        idx=idx,
                        decoded=decoded,
                        client=client,
                        context=context,
                        values=values,
                    )
                elif step.output_type == "json":
                    restored = restore_optional_fields(decoded, schema or {})
                    self._validate_json_output(restored, schema or {})
                    per_step_json[str(idx)] = restored
                    context[f"step.{idx}.json"] = restored
                    if step.expected_out_files:
                        per_step_out_files[str(idx)] = self._process_expected_out_files(
                            step=step,
                            idx=idx,
                            json_output=restored,
                            context=context,
                            client=client,
                        )
                else:
                    text = str(decoded.get("text") or "")
                    per_step_text[str(idx)] = text
                    context[f"step.{idx}.text"] = text

                response_id = str(response.get("id") or "").strip()
                if response_id:
                    context[f"step.{idx}.response_id"] = response_id
                    per_step_response_ids[str(idx)] = response_id
                    last_response_id = response_id
                    if step.deterministic:
                        context_response_ids[step.context_id] = response_id

                if step.deterministic:
                    # Mirror useful values into legacy numbered context for diagnostics/templates.
                    for output in step.outputs:
                        value = values.get(self._value_key(step.id, output.id))
                        if value is None:
                            continue
                        if output.kind == "text":
                            context[f"step.{idx}.text"] = value.get("value", "")
                            if str(idx) not in per_step_text:
                                per_step_text[str(idx)] = str(value.get("value", ""))
                        elif output.kind in ("json", "decision"):
                            context[f"step.{idx}.json"] = value.get("value")
                            per_step_json.setdefault(str(idx), {})[output.id] = value.get("value")
                        elif output.kind == "file":
                            per_step_out_files.setdefault(str(idx), {})[output.id] = value

                executed_step_ids.add(step.id)
                self._primary_responses.pop(primary_key, None)
                self.logger.update_state({"cascade_runtime": self._cache_snapshot(
                    context=context, context_response_ids=context_response_ids, values=values,
                    executed_step_ids=executed_step_ids,
                )})
                completed_record = self.logger.bundle.update_step(
                    current_step_record_id,
                    status="completed",
                    progress=100,
                    finished_at=datetime.now(UTC).isoformat(),
                    human_summary=f"Krok {idx} dokončen.",
                )
                self.logger.event(
                    "cascade.step.ok",
                    {
                        "idx": idx,
                        "step_id": step.id,
                        "title": step_label,
                        "context_id": step.context_id,
                        "response_id": response_id,
                        "outputs": step_summary,
                        "file_ids": file_ids,
                    },
                )
                next_index = self._next_index_for_step(
                    current_index,
                    step,
                    decision_value,
                )
                runtime_snapshot = self._cache_snapshot(
                    context=context,
                    context_response_ids=context_response_ids,
                    values=values,
                    executed_step_ids=executed_step_ids,
                )
                next_step_id = self.cfg.cascade.steps[next_index].id if next_index < len(self.cfg.cascade.steps) else ""
                self._write_runtime_state(
                    {
                        "status": "running",
                        "run_id": run_id,
                        "last_completed_step_id": step.id,
                        "last_completed_step_number": idx,
                        "cache": runtime_snapshot,
                    }
                )
                checkpoint_state = parse_json_strict(Path(self.logger.state_path).read_text(encoding="utf-8"))
                checkpoint_state.update(
                    {
                        "cascade_runtime": runtime_snapshot,
                        "last_completed_step_id": step.id,
                        "next_step_id": next_step_id,
                    }
                )
                self.logger.update_state(
                    {
                        "cascade_runtime": runtime_snapshot,
                        "last_completed_step_id": step.id,
                        "next_step_id": next_step_id,
                    }
                )
                self.logger.checkpoint(
                    "cascade_step_completed",
                    state_snapshot=checkpoint_state,
                    safe_to_continue=not self._cascade_input_missing,
                    reason=(f"Krok {idx} je dokončen a návazný stav je kanonicky uložen."
                            if not self._cascade_input_missing else
                            "Návazný stav existuje, ale některý lokální vstup není kanonicky archivován."),
                    step_id=current_step_record_id,
                    required_artifact_ids=list(dict.fromkeys([
                        *self._cascade_input_artifact_ids,
                        *(completed_record.get("artifact_ids") or []),
                    ])),
                    required_response_ids=list(completed_record.get("response_ids") or []),
                    invalidation_rules=[
                        "Změna signatury zděděného kroku invaliduje checkpoint.",
                        "Chybějící response nebo souborový výstup blokuje pokračování.",
                    ],
                )
                self._emit_status(
                    int((current_index + 1) * 100 / total),
                    100,
                    f"Krok {idx} dokončen",
                )
                self.progress_event.emit(
                    ProgressEvent(
                        progress_keys[current_index],
                        "completed",
                        completed=len(executed_step_ids),
                        total=total,
                        unit="kroků",
                        detail=f"Krok {idx} dokončen.",
                    )
                )
                current_index = next_index
                current_step_record_id = ""

            self.progress_event.emit(ProgressEvent("Uložení výsledků posloupnosti", source="disk"))
            publish_report = self._publish_cascade_outputs()
            final_outputs = self._selected_final_outputs(values)
            text_value = per_step_text.get(str(max(per_step_text, key=int)), "") if per_step_text else ""
            result = {
                "mode": "KASKADA",
                "run_id": run_id,
                "response_id": last_response_id,
                "last_response_id": last_response_id,
                "step_response_ids": per_step_response_ids,
                "step_json_outputs": per_step_json,
                "step_text_outputs": per_step_text,
                "step_out_files": per_step_out_files,
                "final_outputs": final_outputs,
                "publish_report": publish_report,
                "published_files": (
                    publish_report.get("published", [])
                    if isinstance(publish_report, dict)
                    else []
                ),
                "executed_step_ids": sorted(executed_step_ids),
                "text": text_value,
            }
            self.logger.update_state(
                {
                    "status": "completed",
                    "finished_at": time.time(),
                    "last_response_id": last_response_id,
                    "steps_done": len(executed_step_ids),
                    "result": result,
                }
            )
            self.logger.event("cascade.completed", result)
            self._write_runtime_state(
                {
                    "status": "completed",
                    "run_id": run_id,
                    "failed_step_id": "",
                    "failed_step_number": 0,
                    "human_error": "",
                    "technical_error": "",
                    "cache": self._cache_snapshot(
                        context=context,
                        context_response_ids=context_response_ids,
                        values=values,
                        executed_step_ids=executed_step_ids,
                    ),
                    "result": result,
                }
            )
            self.progress_event.emit(ProgressEvent("Uložení výsledků posloupnosti", "completed", source="disk"))
            self.progress_event.emit(ProgressEvent("RUN", "completed"))
            self.finished_ok.emit(result)
        except SubmissionOutcomeUnknown as ex:
            snapshot = self._cache_snapshot(
                context=context, context_response_ids=context_response_ids,
                values=values, executed_step_ids=executed_step_ids,
            )
            unknown = {
                "operation": ex.operation, "method": ex.method, "path": ex.path,
                "request_id": ex.request_id, "step_number": self._failed_step_index + 1,
            }
            state = {
                "status": "submission_unknown", "run_id": run_id,
                "error": str(ex), "unknown_submission": unknown,
                "cascade_runtime": snapshot,
            }
            if self.logger:
                if current_step_record_id:
                    self.logger.bundle.update_step(
                        current_step_record_id, status="submission_unknown",
                        finished_at=datetime.now(UTC).isoformat(),
                        human_summary="Výsledek odeslání není znám; automatické opakování je zakázáno.",
                    )
                self.logger.event("cascade.submission_unknown", unknown)
                self.logger.update_state(state)
                self.logger.checkpoint(
                    "cascade_submission_unknown", state_snapshot=state,
                    safe_to_continue=False,
                    reason="Nejdříve je nutné dohledat výsledek již odeslané operace.",
                )
            self._write_runtime_state({**state, "cache": snapshot})
            if self.logger:
                self.logger.bundle.seal()
            self.progress_event.emit(ProgressEvent("RUN", "submission_unknown", detail=str(ex)))
            self.finished_err.emit(str(ex))
        except Exception as ex:
            if str(ex) in ("STOPPED", "STOP_REQUESTED"):
                if self.logger:
                    if current_step_record_id:
                        self.logger.bundle.update_step(
                            current_step_record_id,
                            status="cancelled",
                            finished_at=datetime.now(UTC).isoformat(),
                            human_summary="Krok byl zrušen uživatelem.",
                        )
                    self.logger.event("cascade.cancelled", {"error": str(ex)})
                    self.logger.update_state(
                        {"status": "cancelled", "finished_at": time.time(), "error": str(ex)}
                    )
                self._write_runtime_state(
                    {
                        "status": "cancelled",
                        "run_id": run_id,
                        "failed_step_id": "",
                        "failed_step_number": 0,
                        "human_error": "Běh kaskády byl zastaven.",
                        "technical_error": str(ex),
                        "cache": self._cache_snapshot(
                            context=context,
                            context_response_ids=context_response_ids,
                            values=values,
                            executed_step_ids=executed_step_ids,
                        ),
                    }
                )
                self.progress_event.emit(ProgressEvent("RUN", "cancelled"))
                self.finished_err.emit(str(ex))
                return

            human = humanize_cascade_error(ex)
            technical = str(ex)
            terminal_status = "submission_unknown" if isinstance(ex, SubmissionOutcomeUnknown) else "failed"
            if self.logger:
                repo = repository_for_logger(self.logger)
                with repo.connect() as db:
                    uncertain = db.execute(
                        "SELECT 1 FROM provider_operations p JOIN work_orders w "
                        "ON w.work_order_hash=p.work_order_hash WHERE w.run_id=? "
                        "AND p.state='submission_unknown' LIMIT 1", (self.logger.run_id,),
                    ).fetchone()
                if uncertain:
                    terminal_status = "submission_unknown"
            failed_step_id = ""
            failed_step_number = 0
            if 0 <= self._failed_step_index < len(self.cfg.cascade.steps):
                failed_step = self.cfg.cascade.steps[self._failed_step_index]
                failed_step_id = failed_step.id
                failed_step_number = self._failed_step_index + 1
            if self.logger:
                if current_step_record_id:
                    self.logger.bundle.update_step(
                        current_step_record_id,
                        status=terminal_status,
                        finished_at=datetime.now(UTC).isoformat(),
                        technical_summary=technical,
                        human_summary=human,
                    )
                self.logger.event(
                    "cascade.failed",
                    {
                        "error": technical,
                        "human_error": human,
                        "failed_step_id": failed_step_id,
                        "failed_step_number": failed_step_number,
                    },
                )
                self.logger.update_state(
                    {
                        "status": terminal_status,
                        "finished_at": time.time(),
                        "error": technical,
                        "human_error": human,
                        "failed_step_id": failed_step_id,
                        "failed_step_number": failed_step_number,
                    }
                )
            self._write_runtime_state(
                {
                    "status": terminal_status,
                    "run_id": run_id,
                    "failed_step_id": failed_step_id,
                    "failed_step_number": failed_step_number,
                    "human_error": human,
                    "technical_error": technical,
                    "cache": self._cache_snapshot(
                        context=context,
                        context_response_ids=context_response_ids,
                        values=values,
                        executed_step_ids=executed_step_ids,
                    ),
                }
            )
            from .user_errors import describe_error
            self.failure_detail.emit(describe_error(ex))
            self.progress_event.emit(ProgressEvent("RUN", terminal_status))
            self.finished_err.emit(human)
