from __future__ import annotations

import base64
import copy
import json
import os
import re
import tempfile
import time
import jsonschema
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal

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
from .contracts import ContractError, validate_paths
from .openai_client import OpenAIClient
from .progress import ProgressEvent
from .request_rules import validate_response_payload
from .retry import CircuitBreaker, with_retry
from .structured_output import (
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
    validate_relative_path,
)


PLACEHOLDER_RE = re.compile(
    r"\{\{\s*step\.(\d+)\.(response_id|json|text|out_file_path|out_file_id)(?::([^}]+))?\s*\}\}"
)


PRESET_MANIFEST_SCHEMA: Dict[str, Any] = {
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


PRESET_PROMPTS_SCHEMA: Dict[str, Any] = {
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
    resume_snapshot: Optional[Dict[str, Any]] = None
    recovery_instruction: str = ""
    lineage: Optional[Dict[str, Any]] = None


class CascadeRunWorker(QThread):
    progress = Signal(int)
    progress_event = Signal(object)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    failure_detail = Signal(object)

    STEP_ATTEMPTS = 3

    def __init__(
        self,
        cfg: CascadeRunConfig,
        settings,
        api_key: str,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        if not self.cfg.out_dir.strip():
            self.cfg.out_dir = self.cfg.cascade.default_out_dir.strip()
        self.settings = copy.deepcopy(settings)
        self.api_key = api_key
        self.breaker = CircuitBreaker(
            settings.retry.circuit_breaker_failures,
            settings.retry.circuit_breaker_cooldown_s,
        )
        self._stop = False
        self.logger: Optional[CascadeLogger] = None
        self._failed_step_index = 0
        self._runtime_cache: Dict[str, Any] = {}

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
            "\n\n[NOVÁ VĚTEV – explicitní pokyn platí pouze pro tento a následující nově prováděné kroky]\n"
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
                resolved = self._resolve_text(str(value or ""), {})
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
                })
        return mappings, missing

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
        return str(base / self._cascade_filename(self.cfg.cascade.name))

    def _read_runtime_state(self) -> Dict[str, Any]:
        path = self._runtime_path()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write_runtime_state(self, patch: Dict[str, Any]) -> None:
        state = self._read_runtime_state()
        state.update(patch)
        state["cascade_name"] = self.cfg.cascade.name
        state["updated_at"] = time.time()
        atomic_write_text(
            self._runtime_path(),
            json.dumps(state, ensure_ascii=False, indent=2, default=str),
        )

    def _resolve_text(self, text: Optional[str], context: Dict[str, Any]) -> str:
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

    def _resolve_json(self, obj: Any, context: Dict[str, Any]) -> Any:
        if isinstance(obj, str):
            return self._resolve_text(obj, context)
        if isinstance(obj, list):
            return [self._resolve_json(value, context) for value in obj]
        if isinstance(obj, dict):
            return {key: self._resolve_json(value, context) for key, value in obj.items()}
        return obj

    def _schema_for_step(self, step: CascadeStep) -> Optional[Dict[str, Any]]:
        if step.deterministic:
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

    def _validate_schema_minimal(self, schema: Dict[str, Any]) -> None:
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

    def _validate_json_output(self, obj: Dict[str, Any], schema: Dict[str, Any]) -> None:
        if schema:
            self._validate_schema_minimal(schema)
        jsonschema.validate(obj, schema)
        if not isinstance(obj, dict):
            raise RuntimeError("JSON výstup musí být objekt.")

    def _normalize_content_parts(self, resolved_content_json: Any, idx: int) -> List[Dict[str, Any]]:
        if isinstance(resolved_content_json, list):
            out: List[Dict[str, Any]] = []
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
    def _extract_input_file_ids(parts: List[Dict[str, Any]]) -> set[str]:
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
    def _decode_file_content(row: Dict[str, Any]) -> bytes:
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

    def _write_files_atomically(
        self,
        rows: List[Dict[str, Any]],
        out_dir: str,
        step_idx: int,
    ) -> Dict[str, Any]:
        out_abs = os.path.abspath(out_dir)
        normalized: List[Tuple[str, bytes]] = []
        for row in rows:
            rel = self._normalize_expected_rel_path(str(row.get("path") or ""))
            dst = safe_join_under_root(out_abs, rel.replace("/", os.sep))
            data = self._decode_file_content(row)
            normalized.append((dst, data))

        ensure_dir(out_abs)
        written: List[Dict[str, Any]] = []
        temp_paths: List[str] = []
        try:
            for dst, data in normalized:
                ensure_dir(os.path.dirname(dst))
                fd, temp_path = tempfile.mkstemp(prefix=".cascade_", dir=os.path.dirname(dst))
                temp_paths.append(temp_path)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, dst)
                temp_paths.remove(temp_path)
                written.append(
                    {
                        "path": os.path.relpath(dst, out_abs).replace(os.sep, "/"),
                        "dst": dst,
                        "bytes": os.path.getsize(dst),
                    }
                )
        finally:
            for temp_path in temp_paths:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        if self.logger:
            for row in written:
                self.logger.record_fs_change(
                    "write",
                    row["path"],
                    row["dst"],
                    after_size=row["bytes"],
                    step_id=str(getattr(self, "_current_step_record_id", "") or ""),
                )
            self.logger.save_json(
                "manifests",
                f"cascade_step_{step_idx:02d}_out_saved_map",
                {"saved": written, "out_dir": out_abs},
                step_id=str(getattr(self, "_current_step_record_id", "") or ""),
            )
        return {"saved": written, "out_dir": out_abs}

    def _save_manifest_to_out(
        self,
        files: List[Dict[str, Any]],
        out_dir: str,
        step_idx: int,
    ) -> Dict[str, Any]:
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
        context: Dict[str, Any],
        client: OpenAIClient,
    ) -> Dict[str, Any]:
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

        normalized_manifest: List[Dict[str, Any]] = []
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
        manifest_paths = {row["path"] for row in normalized_manifest}
        missing = [rel for rel in expected if rel not in manifest_paths]
        if missing:
            raise RuntimeError(
                f"Krok {idx}: v manifestu chybí očekávané soubory: {', '.join(missing)}"
            )

        # Nothing is written before the whole manifest passes validation.
        for row in normalized_manifest:
            self._decode_file_content(row)
        self._save_manifest_to_out(normalized_manifest, out_dir, idx)

        result: Dict[str, Dict[str, str]] = {}
        out_abs = os.path.abspath(out_dir)
        for rel in expected:
            abs_path = safe_join_under_root(out_abs, rel.replace("/", os.sep))
            uploaded = client.upload_file(abs_path, purpose='user_data')
            file_id = str(uploaded.get("id") or "").strip()
            if not file_id:
                raise RuntimeError(f"Krok {idx}: upload souboru nevrátil file_id: {rel}")
            context[f"step.{idx}.out_file_path:{rel}"] = abs_path
            context[f"step.{idx}.out_file_id:{rel}"] = file_id
            result[rel] = {"path": abs_path, "file_id": file_id}
        return result

    def _load_resume_cache(
        self,
        start_index: int,
    ) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, Any], set[str]]:
        if start_index <= 0:
            return {}, {}, {}, set()
        state = self._read_runtime_state()
        cache = self.cfg.resume_snapshot if self.cfg.resume_snapshot is not None else state.get("cache")
        if not isinstance(cache, dict):
            raise CascadeValidationError(
                "Pro spuštění od vybraného kroku chybí předchozí dokončený stav; spusťte kaskádu od začátku."
            )
        signatures = cache.get("step_signatures", {})
        if not isinstance(signatures, dict):
            signatures = {}
        for index in range(start_index):
            step = self.cfg.cascade.steps[index]
            if signatures.get(step.id) != step_signature(step):
                raise CascadeValidationError(
                    f"Předchozí krok {index + 1} se od posledního běhu změnil; spusťte kaskádu nejpozději od tohoto kroku."
                )
        context = cache.get("legacy_context", {})
        context_ids = cache.get("context_response_ids", {})
        values = cache.get("values", {})
        executed = set(cache.get("executed_step_ids", []))
        if not isinstance(context, dict) or not isinstance(context_ids, dict) or not isinstance(values, dict):
            raise CascadeValidationError(
                "Uložený mezistav kaskády je poškozený; spusťte kaskádu od začátku."
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
        values: Dict[str, Any],
        client: OpenAIClient,
    ) -> Tuple[str, List[str]]:
        extra_text: List[str] = []
        file_ids: List[str] = []

        for item in step.inputs:
            if item.source == "text":
                extra_text.append(f"[Vstup: {item.name}]\n{item.value}")
                continue
            if item.source == "local_file":
                path = self._resolve_text(item.value, {})
                if not os.path.isfile(path):
                    raise RuntimeError(f"Vstupní soubor neexistuje: {path}")
                uploaded = client.upload_file(path, purpose='user_data')
                file_id = str(uploaded.get("id") or "").strip()
                if not file_id:
                    raise RuntimeError(f"Upload souboru nevrátil file_id: {path}")
                file_ids.append(file_id)
                continue
            if item.source == "file_id":
                if item.value.strip():
                    file_ids.append(item.value.strip())
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
                        file_ids.append(file_id)
                    elif value.get("path") and os.path.isfile(str(value["path"])):
                        uploaded = client.upload_file(str(value['path']), purpose='user_data')
                        file_id = str(uploaded.get("id") or "").strip()
                        if not file_id:
                            raise RuntimeError("Upload návazného souboru nevrátil file_id.")
                        file_ids.append(file_id)
                    else:
                        raise RuntimeError("Návazný soubor už není dostupný.")
                else:
                    visible = value.get("value") if isinstance(value, dict) and "value" in value else value
                    if not isinstance(visible, str):
                        visible = json.dumps(visible, ensure_ascii=False)
                    extra_text.append(f"[Vstup: {item.name}]\n{visible}")
        text = step.input_text
        if extra_text:
            text = text.rstrip() + "\n\n" + "\n\n".join(extra_text)
        return text, file_ids

    def _deterministic_instructions(self, step: CascadeStep) -> str:
        lines = [
            "Dodrž přesně výstupní kontrakt tohoto kroku.",
            "Nevracej žádné další klíče ani doprovodný text.",
        ]
        for output in step.outputs:
            lines.append("- " + describe_output(output))
            if output.kind == "json":
                lines.append(
                    f"  Hodnotu „{output.name}“ vrať jako validní JSON serializovaný do jednoho textového řetězce."
                )
            if output.kind == "file" and output.file_mode == "modify":
                lines.append(
                    f"  Soubor „{output.name}“ musí být upravenou verzí vybraného vstupního souboru, nikoli novým nesouvisejícím souborem."
                )
        prefix = step.instructions.strip()
        return (prefix + "\n\n" if prefix else "") + "\n".join(lines)

    def _prepare_step(
        self,
        *,
        step: CascadeStep,
        idx: int,
        context: Dict[str, Any],
        context_response_ids: Dict[str, str],
        values: Dict[str, Any],
        client: OpenAIClient,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
        if step.deterministic:
            resolved_input_text, file_ids = self._resolve_deterministic_inputs(
                step=step,
                idx=idx,
                values=values,
                client=client,
            )
            if self.cfg.recovery_instruction:
                resolved_input_text += self._recovery_suffix()
            content_parts: List[Dict[str, Any]] = [
                {"type": "input_text", "text": resolved_input_text}
            ]
            existing_file_ids = set()
            for file_id in file_ids:
                if file_id and file_id not in existing_file_ids:
                    content_parts.append({"type": "input_file", "file_id": file_id})
                    existing_file_ids.add(file_id)
            schema = self._schema_for_step(step)
            payload: Dict[str, Any] = {
                "model": step.model,
                "instructions": self._deterministic_instructions(step),
                "input": [{"type": "message", "role": "user", "content": content_parts}],
                "text": response_format(f"cascade_step_{idx:02d}_det", schema),
            }
            previous_id = context_response_ids.get(step.context_id, "")
            if previous_id:
                payload["previous_response_id"] = previous_id
            if step.temperature is not None:
                payload["temperature"] = float(step.temperature)
            validate_response_payload(payload)
            client.validate_prepared_payload(payload)
            return payload, schema or {}, file_ids

        # Legacy compatibility path.
        file_ids: List[str] = []
        for expression in step.files_existing_ids or []:
            resolved = self._resolve_text(expression, context).strip()
            if resolved:
                file_ids.append(resolved)
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
        for file_id in file_ids:
            if file_id and file_id not in preflight_existing:
                preflight_parts.append({"type": "input_file", "file_id": file_id})
                preflight_existing.add(file_id)
        if step.files_local_paths and "file_local_validation" not in preflight_existing:
            preflight_parts.append({"type": "input_file", "file_id": "file_local_validation"})
        preflight_payload = {
            "model": step.model,
            "instructions": resolved_instructions,
            "input": [{"type": "message", "role": "user", "content": preflight_parts}],
            "text": text_format(),
        }
        if step.temperature is not None:
            preflight_payload["temperature"] = float(step.temperature)
        validate_response_payload(preflight_payload)
        client.validate_prepared_payload(preflight_payload)

        for local_path in step.files_local_paths or []:
            resolved_path = self._resolve_text(local_path, context)
            if not os.path.isfile(resolved_path):
                raise RuntimeError(f"Lokální soubor neexistuje: {resolved_path}")
            uploaded = client.upload_file(resolved_path, purpose='user_data')
            file_id = str(uploaded.get("id") or "").strip()
            if not file_id:
                raise RuntimeError(f"Upload souboru nevrátil file_id: {resolved_path}")
            file_ids.append(file_id)

        existing = self._extract_input_file_ids(content_parts)
        for file_id in file_ids:
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
                )
            wire_format = response_format(f"cascade_step_{idx:02d}_schema", schema)
        else:
            wire_format = text_format()

        payload = {
            "model": step.model,
            "instructions": resolved_instructions,
            "input": [{"type": "message", "role": "user", "content": content_parts}],
            "text": wire_format,
        }
        if step.temperature is not None:
            payload["temperature"] = float(step.temperature)
        resolved_prev = self._resolve_text(step.previous_response_id_expr or "", context).strip()
        if resolved_prev:
            payload["previous_response_id"] = resolved_prev
        validate_response_payload(payload)
        client.validate_prepared_payload(payload)
        return payload, schema or {}, file_ids

    def _process_deterministic_output(
        self,
        *,
        step: CascadeStep,
        idx: int,
        decoded: Dict[str, Any],
        client: OpenAIClient,
        context: Dict[str, Any],
        values: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        out_dir = self._select_out_dir_for_step()
        file_rows: List[Tuple[CascadeOutput, Dict[str, Any]]] = []
        decision_value: Optional[str] = None
        summary: Dict[str, Any] = {}

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
                if not isinstance(raw, str):
                    raise ContractError(f"Krok {idx}: výstup „{output.name}“ musí obsahovat JSON text.")
                try:
                    parsed = json.loads(raw)
                except ValueError as exc:
                    raise ContractError(
                        f"Krok {idx}: výstup „{output.name}“ neobsahuje platný JSON."
                    ) from exc
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
            self._write_files_atomically(rows, out_dir, idx)
            out_abs = os.path.abspath(out_dir)
            for output, _row in file_rows:
                rel = output.file_name
                path = safe_join_under_root(out_abs, rel.replace("/", os.sep))
                uploaded = client.upload_file(path, purpose='user_data')
                file_id = str(uploaded.get("id") or "").strip()
                if not file_id:
                    raise RuntimeError(f"Krok {idx}: upload výstupu nevrátil file_id: {rel}")
                value = {"kind": "file", "path": path, "file_id": file_id, "file_type": output.file_type}
                values[self._value_key(step.id, output.id)] = value
                summary[output.id] = value
                context[f"step.{idx}.out_file_path:{rel}"] = path
                context[f"step.{idx}.out_file_id:{rel}"] = file_id
        return summary, decision_value

    def _next_index_for_step(
        self,
        current_index: int,
        step: CascadeStep,
        decision_value: Optional[str],
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
        context: Dict[str, Any],
        context_response_ids: Dict[str, str],
        values: Dict[str, Any],
        executed_step_ids: set[str],
    ) -> Dict[str, Any]:
        return {
            "legacy_context": copy.deepcopy(context),
            "context_response_ids": copy.deepcopy(context_response_ids),
            "values": copy.deepcopy(values),
            "executed_step_ids": sorted(executed_step_ids),
            "step_signatures": {
                step.id: step_signature(step) for step in self.cfg.cascade.steps
            },
        }

    def _selected_final_outputs(self, values: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for ref in self.cfg.cascade.final_outputs:
            key = self._value_key(ref.step_id, ref.output_id)
            if key in values:
                result[key] = copy.deepcopy(values[key])
        return result

    def run(self):
        run_id = self.cfg.run_id or new_run_id()
        current_index = 0
        current_step_record_id = ""
        context: Dict[str, Any] = {}
        context_response_ids: Dict[str, str] = {}
        values: Dict[str, Any] = {}
        executed_step_ids: set[str] = set()
        try:
            validate_cascade_definition(self.cfg.cascade, strict=True)
            self.logger = CascadeLogger(
                self.settings.log_dir,
                run_id,
                project_name=self.cfg.project,
            )
            if self.cfg.lineage:
                lineage = dict(self.cfg.lineage)
                source_run_id = str(lineage.pop("source_run_id", "") or "")
                relation_type = str(lineage.pop("relation_type", "") or "")
                if source_run_id and relation_type:
                    self.logger.record_lineage(source_run_id, relation_type, **lineage)
            input_artifacts, missing_input_artifacts = self._archive_cascade_inputs()
            self._cascade_input_artifact_ids = [row["artifact_id"] for row in input_artifacts]
            self._cascade_input_missing = list(missing_input_artifacts)
            start_index = 0
            if self.cfg.cascade.run_from_step_id:
                start_index = self.cfg.cascade.step_index(self.cfg.cascade.run_from_step_id)
                if start_index < 0:
                    raise CascadeValidationError("Vybraný počáteční krok už neexistuje.")
            context, context_response_ids, values, executed_step_ids = self._load_resume_cache(
                start_index
            )
            # All values and response lineage from the selected step onward become invalid.
            invalid_step_ids = {
                step.id for step in self.cfg.cascade.steps[start_index:]
            }
            values = {
                key: value
                for key, value in values.items()
                if key.split("|", 1)[0] not in invalid_step_ids
            }
            executed_step_ids = {
                step_id for step_id in executed_step_ids if step_id not in invalid_step_ids
            }
            if start_index > 0:
                context = {
                    key: value
                    for key, value in context.items()
                    if not (
                        key.startswith("step.")
                        and key.split(".", 2)[1].isdigit()
                        and int(key.split(".", 2)[1]) >= start_index + 1
                    )
                }
                context_response_ids = {}
                for prior_index in range(start_index):
                    prior_step = self.cfg.cascade.steps[prior_index]
                    response_id = str(
                        context.get(f"step.{prior_index + 1}.response_id") or ""
                    ).strip()
                    if response_id:
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
            initial_state = json.loads(Path(self.logger.state_path).read_text(encoding="utf-8"))
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
            client.configure_validation(self.settings)
            client.stopped = lambda: self._stop

            total = max(1, len(self.cfg.cascade.steps))
            per_step_response_ids: Dict[str, str] = {}
            per_step_text: Dict[str, str] = {}
            per_step_json: Dict[str, Any] = {}
            per_step_out_files: Dict[str, Dict[str, Any]] = {}
            last_response_id = ""

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
                        "KASKÁDA",
                        completed=len(executed_step_ids),
                        total=total,
                        unit="kroků",
                        detail=step_label,
                    )
                )

                last_exc: Optional[BaseException] = None
                decoded: Dict[str, Any] = {}
                response: Dict[str, Any] = {}
                schema: Dict[str, Any] = {}
                file_ids: List[str] = []
                step_summary: Dict[str, Any] = {}
                decision_value: Optional[str] = None

                for attempt in range(1, self.STEP_ATTEMPTS + 1):
                    self._check_stop()
                    try:
                        payload, schema, file_ids = self._prepare_step(
                            step=step,
                            idx=idx,
                            context=context,
                            context_response_ids=context_response_ids,
                            values=values,
                            client=client,
                        )
                        self.logger.save_json(
                            "requests",
                            f"cascade_step_{idx:02d}_attempt_{attempt}",
                            payload,
                            step_id=current_step_record_id,
                        )
                        self._emit_status(
                            base_p,
                            50,
                            f"Krok {idx}: požadavek na OpenAI · pokus {attempt}/{self.STEP_ATTEMPTS}",
                        )
                        response = client.create_response(payload)
                        self.logger.save_json(
                            "responses",
                            f"cascade_step_{idx:02d}_attempt_{attempt}",
                            response,
                            step_id=current_step_record_id,
                        )
                        decoded = validate_output(response, payload)

                        if step.deterministic:
                            self._validate_json_output(decoded, schema)
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

                        last_exc = None
                        break
                    except Exception as exc:
                        if str(exc) in ("STOPPED", "STOP_REQUESTED"):
                            raise
                        last_exc = exc
                        self.logger.event(
                            "cascade.step.attempt_failed",
                            {
                                "idx": idx,
                                "attempt": attempt,
                                "error": str(exc),
                            },
                        )
                        if attempt < self.STEP_ATTEMPTS:
                            self._emit_status(
                                base_p,
                                50,
                                f"Krok {idx}: výstup neprošel kontrolou, opakuji ({attempt + 1}/{self.STEP_ATTEMPTS}).",
                            )
                            time.sleep(min(2.0, 0.35 * attempt))
                if last_exc is not None:
                    raise last_exc

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
                completed_record = self.logger.bundle.update_step(
                    current_step_record_id,
                    status="completed",
                    progress=100,
                    finished_at=datetime.now(timezone.utc).isoformat(),
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
                checkpoint_state = json.loads(Path(self.logger.state_path).read_text(encoding="utf-8"))
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
                        "KASKÁDA",
                        completed=len(executed_step_ids),
                        total=total,
                        unit="kroků",
                        detail=f"Krok {idx} dokončen.",
                    )
                )
                current_index = next_index
                current_step_record_id = ""

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
            self.progress_event.emit(ProgressEvent("RUN", "completed"))
            self.finished_ok.emit(result)
        except Exception as ex:
            if str(ex) in ("STOPPED", "STOP_REQUESTED"):
                if self.logger:
                    if current_step_record_id:
                        self.logger.bundle.update_step(
                            current_step_record_id,
                            status="cancelled",
                            finished_at=datetime.now(timezone.utc).isoformat(),
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
                        status="failed",
                        finished_at=datetime.now(timezone.utc).isoformat(),
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
                        "status": "failed",
                        "finished_at": time.time(),
                        "error": technical,
                        "human_error": human,
                        "failed_step_id": failed_step_id,
                        "failed_step_number": failed_step_number,
                    }
                )
            self._write_runtime_state(
                {
                    "status": "failed",
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
            self.progress_event.emit(ProgressEvent("RUN", "failed"))
            self.finished_err.emit(human)
