from __future__ import annotations

import copy
import json
import os
import re
import time
import jsonschema
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal
from .progress import ProgressEvent

from .cascade_log import CascadeLogger
from .cascade_types import CascadeDefinition, CascadeStep
from .contracts import ContractError, validate_paths
from .request_rules import validate_response_payload
from .structured_output import resolve_schema, response_format, text_format, validate_output, restore_optional_fields
from .openai_client import OpenAIClient
from .retry import CircuitBreaker, with_retry
from .receipt import Receipt
from .pricing import compute_cost, PriceTable
from .utils import ensure_dir, new_run_id, safe_join_under_root, validate_relative_path, atomic_write_text


PLACEHOLDER_RE = re.compile(r"\{\{\s*step\.(\d+)\.(response_id|json|out_file_path|out_file_id)(?::([^}]+))?\s*\}\}")


PRESET_MANIFEST_SCHEMA: Dict[str, Any] = {
    "description": "Souborový manifest pro přímé uložení do OUT (kompatibilní s interním save pipeline).",
    "type": "object",
    "required": ["files"],
    "additionalProperties": False,
    "properties": {
        "mode": {"type": "string", "description": "Volitelné označení režimu (např. patches)."},
        "root": {"type": "string", "description": "Volitelný kořen projektu pro orientaci."},
        "files": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["path", "content"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "description": "Relativní cesta souboru vůči OUT."},
                    "content": {"type": "string", "description": "Textový obsah souboru (UTF-8)."},
                    "purpose": {"type": "string", "description": "Volitelný účel souboru (metadata)."},
                    "encoding": {
                        "type": "string",
                        "description": "Volitelné metadata o kódování, typicky utf-8 nebo base64.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Volitelná akce pro kompatibilitu (např. add/modify).",
                    },
                },
            },
        },
        "note": {"type": "string", "description": "Volitelná poznámka k dávce změn."},
    },
}

PRESET_PROMPTS_SCHEMA: Dict[str, Any] = {
    "description": "Definice kaskády promptů; JSON lze rovnou uložit a načíst v Kaskádě.",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "version": {
            "type": "integer",
            "description": "Verze CascadeDefinition (kladné celé číslo, běžně 1).",
        },
        "name": {
            "type": "string",
            "description": "Název kaskády pro zobrazení v UI.",
        },
        "created_at": {
            "type": "number",
            "description": "Volitelné unix timestamp vytvoření (float).",
        },
        "updated_at": {
            "type": "number",
            "description": "Volitelné unix timestamp poslední změny (float).",
        },
        "default_out_dir": {
            "type": "string",
            "description": "Volitelný fallback OUT adresář pro běh Kaskády.",
        },
        "steps": {
            "type": "array",
            "description": "Sekvence kroků kompatibilních s CascadeStep.from_dict().",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "description": "Krátký název kroku."},
                    "model": {
                        "type": "string",
                        "description": "Model pro konkrétní krok; zvol podle účelu (plánování vs. generování kódu).",
                    },
                    "temperature": {"type": ["number", "null"]},
                    "instructions": {
                        "type": "string",
                        "description": "Pole instructions (developer-level instrukce API requestu).",
                    },
                    "input_text": {
                        "type": "string",
                        "description": "Jednoduchý text uživatelského vstupu. Použij když neposíláš strukturované content parts.",
                    },
                    "input_content_json": {
                        "type": ["array", "object", "null"],
                        "description": (
                            "Volitelné Responses API content parts (dict/list). "
                            "Pokud je vyplněno, odešle se 1:1 do payload[\"input\"][user].content. "
                            "Používej pro input_file, multimodální části nebo přesnou strukturu."
                        ),
                    },
                    "files_existing_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "files_local_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "previous_response_id_expr": {
                        "type": ["string", "null"],
                        "description": (
                            "Volitelný výraz pro previous_response_id. "
                            "Podporované výrazy: {{step.N.response_id}}, {{step.N.json}}, {{step.N.out_file_id:REL_PATH}} a {{step.N.out_file_path:REL_PATH}}."
                        ),
                    },
                    "output_type": {"type": "string", "enum": ["text", "json"]},
                    "output_schema_kind": {
                        "type": ["string", "null"],
                        "enum": ["manifest", "prompts", "custom", None],
                    },
                    "output_schema_custom": {"type": ["object", "null"]},
                    "expected_out_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Volitelné relativní cesty souborů očekávaných v OUT manifestu tohoto kroku.",
                    },
                },
                "required": [
                    "title",
                    "model",
                    "temperature",
                    "instructions",
                    "input_text",
                    "input_content_json",
                    "files_existing_ids",
                    "files_local_paths",
                    "previous_response_id_expr",
                    "output_type",
                    "output_schema_kind",
                    "output_schema_custom",
                    "expected_out_files",
                ],
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
            "text": "Return ONLY valid JSON. Do not include any extra text outside JSON.",
        }
    ],
}

PROMPTS_JSON_DEVELOPER_MESSAGE = {
    "type": "message",
    "role": "developer",
    "content": [
        {
            "type": "input_text",
            "text": (
                "Return ONLY valid JSON matching the schema exactly (no markdown, no prose, no extra keys). "
                "The output must be a loadable CascadeDefinition with version, name and steps compatible with CascadeStep. "
                "Use steps[].instructions for developer-style behavior and steps[].input_text for plain user text; "
                "use steps[].input_content_json only when you need structured Responses API content parts sent 1:1. "
                "When chaining future values, use placeholders like {{step.N.response_id}} or {{step.N.json}}; if supported by runtime, you may also use {{step.N.out_file_id:REL_PATH}} and {{step.N.out_file_path:REL_PATH}}. "
                "Recommend an appropriate model in each step.model (e.g., lighter model for planning, stronger for code generation)."
            ),
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


class CascadeRunWorker(QThread):
    progress = Signal(int)
    progress_event = Signal(object)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)

    def __init__(
        self,
        cfg: CascadeRunConfig,
        settings,
        api_key: str,
        receipt_db=None,
        price_table=None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        if not self.cfg.out_dir.strip():
            self.cfg.out_dir = self.cfg.cascade.default_out_dir.strip()
        self.settings = copy.deepcopy(settings)
        self.api_key = api_key
        self.db = receipt_db
        self.price_table = price_table
        self.breaker = CircuitBreaker(settings.retry.circuit_breaker_failures, settings.retry.circuit_breaker_cooldown_s)
        self._stop = False
        self.logger: Optional[CascadeLogger] = None

    def request_stop(self):
        self._stop = True

    def _check_stop(self):
        if self._stop:
            raise RuntimeError("STOP_REQUESTED")

    def _ts(self) -> str:
        return time.strftime("%Y%m%d %H%M%S")

    def _emit_status(self, p: int, sp: int, text: str) -> None:
        self.progress.emit(p)
        self.subprogress.emit(sp)
        self.status.emit(text)
        self.logline.emit(f"{self._ts()} | {text}")

    def _resolve_text(self, text: Optional[str], context: Dict[str, Any]) -> str:
        if not text:
            return ""

        def repl(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            key = match.group(2)
            rel_suffix = (match.group(3) or "").strip()
            if key == "response_id":
                storage_key = f"step.{idx}.response_id"
                if not context.get(storage_key):
                    raise ContractError(f"Chybí hodnota odkazu: {storage_key}")
                return str(context[storage_key])
            if key == "json":
                storage_key = f"step.{idx}.json"
                if storage_key not in context:
                    raise ContractError(f"Chybí hodnota odkazu: {storage_key}")
                val = context[storage_key]
                if isinstance(val, str):
                    return val
                return json.dumps(val, ensure_ascii=False)
            if key in ("out_file_path", "out_file_id"):
                if not rel_suffix:
                    raise ContractError("Odkaz na výstupní soubor vyžaduje relativní cestu.")
                validate_relative_path(rel_suffix)
                norm_rel = rel_suffix
                storage_key = f"step.{idx}.{key}:{norm_rel}"
                if not context.get(storage_key):
                    raise ContractError(f"Chybí hodnota odkazu: {storage_key}")
                return str(context[storage_key])
            return ""

        return PLACEHOLDER_RE.sub(repl, text)

    def _resolve_json(self, obj: Any, context: Dict[str, Any]) -> Any:
        if isinstance(obj, str):
            return self._resolve_text(obj, context)
        if isinstance(obj, list):
            return [self._resolve_json(x, context) for x in obj]
        if isinstance(obj, dict):
            return {k: self._resolve_json(v, context) for k, v in obj.items()}
        return obj

    def _schema_for_step(self, step: CascadeStep) -> Optional[Dict[str, Any]]:
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
        def check_refs(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ("$ref", "$dynamicRef") and (not isinstance(child, str) or not child.startswith("#")):
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
        required = schema.get("required")
        if isinstance(required, list):
            missing = [k for k in required if k not in obj]
            if missing:
                raise RuntimeError(f"JSON output missing required keys: {', '.join(missing)}")
        props = schema.get("properties")
        if isinstance(props, dict):
            for k, meta in props.items():
                if k not in obj:
                    continue
                expected_type = meta.get("type") if isinstance(meta, dict) else None
                val = obj.get(k)
                if expected_type == "array" and not isinstance(val, list):
                    raise RuntimeError(f"JSON key '{k}' musí být array")
                if expected_type == "object" and not isinstance(val, dict):
                    raise RuntimeError(f"JSON key '{k}' musí být object")
                if expected_type == "string" and not isinstance(val, str):
                    raise RuntimeError(f"JSON key '{k}' musí být string")

    def _normalize_content_parts(self, resolved_content_json: Any, idx: int) -> List[Dict[str, Any]]:
        if isinstance(resolved_content_json, list):
            out: List[Dict[str, Any]] = []
            for part in resolved_content_json:
                if not isinstance(part, dict):
                    raise RuntimeError(f"input_content_json list musí obsahovat object part (krok {idx})")
                out.append(part)
            return out
        if isinstance(resolved_content_json, dict):
            return [resolved_content_json]
        raise RuntimeError(f"input_content_json musí být object nebo list (krok {idx})")

    def _extract_input_file_ids(self, parts: List[Dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for part in parts:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "") != "input_file":
                continue
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

    def _save_manifest_to_out(self, files: List[Dict[str, Any]], out_dir: str, step_idx: int) -> Dict[str, Any]:
        out_abs = os.path.abspath(out_dir)
        validate_paths(files)
        for row in files:
            safe_join_under_root(out_abs, row["path"])
            if not isinstance(row.get("content"), str):
                raise ContractError("Obsah výstupního souboru musí být text.")
        ensure_dir(out_abs)
        saved: List[Dict[str, Any]] = []
        for row in files:
            rel = self._normalize_expected_rel_path(str(row.get("path") or ""))
            content = str(row.get("content") or "")
            dst = safe_join_under_root(out_abs, rel.replace("/", os.sep))
            ensure_dir(os.path.dirname(dst))
            atomic_write_text(dst, content)
            saved.append({"path": rel, "dst": dst, "bytes": os.path.getsize(dst)})
        self.logger.save_json("manifests", f"cascade_step_{step_idx:02d}_out_saved_map", {"saved": saved, "out_dir": out_abs})
        return {"saved": saved, "out_dir": out_abs}

    def _process_expected_out_files(
        self,
        *,
        step: CascadeStep,
        idx: int,
        json_output: Any,
        context: Dict[str, Any],
        client: OpenAIClient,
    ) -> Dict[str, Any]:
        expected = [self._normalize_expected_rel_path(x) for x in (step.expected_out_files or []) if str(x).strip()]
        if not expected:
            return {}
        out_dir = self._select_out_dir_for_step()
        if not out_dir:
            raise RuntimeError(
                f"Krok {idx}: expected_out_files vyžaduje OUT adresář. Nastav RUN OUT nebo default_out_dir v definici Kaskády."
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
            rel = self._normalize_expected_rel_path(row.get("path"))
            if not isinstance(row.get("content"), str):
                raise ContractError("Obsah výstupního souboru musí být text.")
            normalized_manifest.append({
                "path": rel,
                "content": str(row.get("content") or ""),
                "purpose": row.get("purpose"),
                "encoding": row.get("encoding"),
                "mode": row.get("mode"),
            })
        validate_paths(normalized_manifest)

        manifest_paths = {row["path"] for row in normalized_manifest}
        missing_manifest = [rel for rel in expected if rel not in manifest_paths]
        if missing_manifest:
            raise RuntimeError(
                f"Krok {idx}: v manifestu chybí expected soubory: {', '.join(missing_manifest)}"
            )

        self._save_manifest_to_out(normalized_manifest, out_dir, idx)

        out_abs = os.path.abspath(out_dir)
        out_files: Dict[str, Dict[str, str]] = {}
        for rel in expected:
            abs_path = safe_join_under_root(out_abs, rel.replace("/", os.sep))
            if not os.path.isfile(abs_path):
                raise RuntimeError(f"Krok {idx}: expected soubor neexistuje po uložení: {rel}")
            up = with_retry(lambda p=abs_path: client.upload_file(p, purpose="user_data"), self.settings.retry, self.breaker)
            fid = str(up.get("id") or "").strip()
            if not fid:
                raise RuntimeError(f"Krok {idx}: upload expected souboru nevrátil file_id: {rel}")
            context[f"step.{idx}.out_file_path:{rel}"] = abs_path
            context[f"step.{idx}.out_file_id:{rel}"] = fid
            out_files[rel] = {"path": abs_path, "file_id": fid}
            self.logger.event("cascade.step.out_file.upload", {"idx": idx, "path": rel, "abs_path": abs_path, "file_id": fid})
        return out_files

    def run(self):
        run_id = self.cfg.run_id or new_run_id()
        try:
            self.logger = CascadeLogger(self.settings.log_dir, run_id, project_name=self.cfg.project)
            if not self.cfg.cascade.steps:
                raise ValueError("Kaskáda musí obsahovat alespoň jeden krok.")
            for step_index, step in enumerate(self.cfg.cascade.steps, 1):
                if step.expected_out_files and step.output_type == "text":
                    step.output_type = "json"
                    step.output_schema_kind = "manifest"
                probe_payload = {"model": step.model, "input": "kontrola"}
                if step.temperature is not None:
                    probe_payload["temperature"] = step.temperature
                validate_response_payload(probe_payload)
                schema = self._schema_for_step(step)
                if schema is not None:
                    self._validate_schema_minimal(schema)
                if step.expected_out_files:
                    if step.output_type != "json":
                        raise ValueError("Výstupní soubory vyžadují JSON manifest.")
                    validate_paths([{"path": path} for path in step.expected_out_files])
                for match in PLACEHOLDER_RE.finditer(json.dumps(step.to_dict())):
                    if not 1 <= int(match.group(1)) < step_index:
                        raise ValueError("Krok může odkazovat pouze na předchozí kroky.")
            self.logger.update_state(
                {
                    "status": "running",
                    "started_at": time.time(),
                    "mode": "KASKADA",
                    "project": self.cfg.project,
                    "out_dir": self.cfg.out_dir,
                    "in_dir": self.cfg.in_dir,
                    "cascade_name": self.cfg.cascade.name,
                    "steps": len(self.cfg.cascade.steps or []),
                }
            )
            self._emit_status(1, 0, f"KASKÁDA start: {self.cfg.cascade.name}")
            client = OpenAIClient(self.api_key, timeout_s=self.settings.response_timeout_s)
            client.configure_validation(self.settings, getattr(self, "cost_control", None))
            prepared_schemas = {}
            per_step_text = {}
            for idx, step in enumerate(self.cfg.cascade.steps, 1):
                check = {"model": step.model, "input": "kontrola", "text": text_format()}
                if step.temperature is not None:
                    check["temperature"] = step.temperature
                if step.previous_response_id_expr:
                    check["previous_response_id"] = "resp_preflight"
                client.preflight_response(check)
                if step.output_type == "json":
                    original = self._schema_for_step(step)
                    if step.output_schema_kind == "prompts":
                        original = copy.deepcopy(PRESET_PROMPTS_SCHEMA)
                        props = original["properties"]["steps"]["items"]["properties"]
                        for field in ("input_content_json", "output_schema_custom"):
                            props[field] = {"type": ["string", "null"], "description": "JSON serializovaný do textu, nebo null."}
                    prepared_schemas[idx] = resolve_schema(client, step.model, step.instructions + "\n" + step.input_text,
                        original, [s.to_dict() for s in self.cfg.cascade.steps[idx:]], getattr(self, "cost_control", None))
                    self.logger.save_json("misc", f"schema_{idx}", prepared_schemas[idx])
            context: Dict[str, Any] = {}
            per_step_response_ids: Dict[str, str] = {}
            per_step_json: Dict[str, Any] = {}
            per_step_out_files: Dict[str, Dict[str, Dict[str, str]]] = {}
            last_response_id = ""

            total = max(1, len(self.cfg.cascade.steps or []))
            for idx, raw_step in enumerate(self.cfg.cascade.steps or [], start=1):
                self._check_stop()
                step = CascadeStep.from_dict(raw_step.to_dict())
                step_label = step.title or f"Step {idx}"
                base_p = int((idx - 1) * 100 / total)
                self._emit_status(base_p, 0, f"Krok {idx}/{total}: {step_label}")
                self.progress_event.emit(ProgressEvent("KASKÁDA", completed=idx - 1, total=total, unit="kroků", detail=step_label))
                self.logger.event("cascade.step.start", {"idx": idx, "title": step_label, "model": step.model})

                file_ids: List[str] = []
                for fid_expr in step.files_existing_ids or []:
                    resolved_fid = self._resolve_text(fid_expr, context).strip()
                    if resolved_fid:
                        file_ids.append(resolved_fid)
                resolved_instructions = self._resolve_text(step.instructions, context)
                resolved_input_text = self._resolve_text(step.input_text, context)
                resolved_prev_expr = self._resolve_text(step.previous_response_id_expr or "", context).strip()
                resolved_content_json = self._resolve_json(step.input_content_json, context) if step.input_content_json is not None else None

                if resolved_content_json is not None:
                    content_parts = self._normalize_content_parts(resolved_content_json, idx)
                else:
                    content_parts = [{"type": "input_text", "text": resolved_input_text}]

                check_parts = copy.deepcopy(content_parts)
                check_parts += [{"type": "input_file", "file_id": fid} for fid in file_ids]
                if step.files_local_paths:
                    check_parts.append({"type": "input_file", "file_id": "file_preflight"})
                precheck = {"model": step.model, "instructions": resolved_instructions,
                    "input": [{"role": "user", "content": check_parts}],
                    "text": response_format(f"cascade_step_{idx:02d}_schema", prepared_schemas[idx]) if step.output_type == "json" else text_format()}
                if step.temperature is not None:
                    precheck["temperature"] = step.temperature
                if resolved_prev_expr:
                    precheck["previous_response_id"] = resolved_prev_expr
                validate_response_payload(precheck)
                client.validate_access(precheck)
                for local_path in step.files_local_paths or []:
                    self._check_stop()
                    resolved_path = self._resolve_text(local_path, context)
                    if not resolved_path:
                        continue
                    if not os.path.isfile(resolved_path):
                        raise RuntimeError(f"Lokální soubor neexistuje: {resolved_path}")
                    self._emit_status(base_p, 20, f"Upload souboru pro krok {idx}: {os.path.basename(resolved_path)}")
                    self.logger.event("cascade.step.file_upload.start", {"idx": idx, "path": resolved_path})
                    up = with_retry(lambda p=resolved_path: client.upload_file(p, purpose="user_data"), self.settings.retry, self.breaker)
                    fid = str(up.get("id") or "").strip()
                    if not fid:
                        raise RuntimeError(f"Upload souboru nevrátil file_id: {resolved_path}")
                    file_ids.append(fid)
                    self.logger.event("cascade.step.file_upload.ok", {"idx": idx, "path": resolved_path, "file_id": fid})

                existing_file_ids = self._extract_input_file_ids(content_parts)
                for fid in file_ids:
                    if not fid or fid in existing_file_ids:
                        continue
                    content_parts.append({"type": "input_file", "file_id": fid})
                    existing_file_ids.add(fid)

                input_messages: List[Dict[str, Any]] = []

                payload: Dict[str, Any] = {
                    "model": step.model,
                    "instructions": resolved_instructions,
                }
                if step.temperature is not None:
                    payload["temperature"] = float(step.temperature)
                if resolved_prev_expr:
                    payload["previous_response_id"] = resolved_prev_expr

                schema = self._schema_for_step(step)
                payload["text"] = response_format(f"cascade_step_{idx:02d}_schema", prepared_schemas[idx]) if step.output_type == "json" else text_format()
                if step.output_type == "json":
                    input_messages.append(copy.deepcopy(PROMPTS_JSON_DEVELOPER_MESSAGE if step.output_schema_kind == "prompts" else JSON_ONLY_DEVELOPER_MESSAGE))

                input_messages.append({"type": "message", "role": "user", "content": content_parts})
                payload["input"] = input_messages

                self.logger.save_json("requests", f"cascade_step_{idx:02d}", payload)
                self._emit_status(base_p, 55, f"OpenAI request krok {idx}")
                self.progress_event.emit(ProgressEvent("KASKÁDA", "waiting", detail=f"Krok {idx}: čekám na API."))
                controller = getattr(self, "cost_control", None)
                response = controller.execute(client, payload, stage=f"STEP_{idx}") if controller else client.create_response(payload)
                if isinstance(response.get("usage"), dict):
                    response["usage"].update(_reasoning=payload.get("reasoning"), _completed=response.get("status") == "completed")
                self.logger.save_json("responses", f"cascade_step_{idx:02d}", response)
                if self.db is not None:
                    usage = response.get("usage") or {}
                    model = response.get("model") or step.model
                    prices = self.price_table or PriceTable.builtin_fallback()
                    row = prices.get(model)
                    inp, out = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
                    total_cost, tool_cost, storage_cost = compute_cost(row, inp, out, usage=usage)
                    self.db.insert(Receipt(
                        run_id=run_id, created_at=time.time(), project=self.cfg.project,
                        model=model, mode="KASKADA", flow_type=f"STEP_{idx}",
                        response_id=response.get("id"), batch_id=None,
                        input_tokens=inp, output_tokens=out, tool_cost=tool_cost,
                        storage_cost=storage_cost, total_cost=total_cost,
                        pricing_verified=prices.is_verified(model), notes=step_label,
                        log_paths={"run_dir": self.logger.paths.run_dir}, usage=usage,
                        pricing_snapshot=row.rates().snapshot() if row else {},
                    ))

                response_id = str(response.get("id") or "").strip()
                if response_id:
                    context[f"step.{idx}.response_id"] = response_id
                    per_step_response_ids[str(idx)] = response_id
                    last_response_id = response_id

                parsed_json: Optional[Dict[str, Any]] = None
                decoded = validate_output(response, payload)
                if step.output_type == "text":
                    self.logger.save_json("misc", f"step_{idx}_text", {"text": decoded["text"]})
                    per_step_text[str(idx)] = decoded["text"]
                if step.output_type == "json":
                    parsed_json = restore_optional_fields(decoded, schema or {})
                    if step.output_schema_kind == "prompts":
                        for generated_step in parsed_json.get("steps", []):
                            for field in ("input_content_json", "output_schema_custom"):
                                if isinstance(generated_step.get(field), str):
                                    generated_step[field] = json.loads(generated_step[field])
                    self._validate_json_output(parsed_json, schema or {})
                    context[f"step.{idx}.json"] = parsed_json
                    per_step_json[str(idx)] = parsed_json
                    self.logger.save_json("misc", f"cascade_step_{idx:02d}_json", parsed_json)

                expected_map = self._process_expected_out_files(
                    step=step,
                    idx=idx,
                    json_output=parsed_json,
                    context=context,
                    client=client,
                )
                if expected_map:
                    per_step_out_files[str(idx)] = expected_map

                self.logger.event(
                    "cascade.step.ok",
                    {
                        "idx": idx,
                        "title": step_label,
                        "response_id": response_id,
                        "json_output": bool(step.output_type == "json"),
                        "file_ids": file_ids,
                        "expected_out_files": list(step.expected_out_files or []),
                    },
                )
                self._emit_status(int(idx * 100 / total), 100, f"Krok {idx} dokončen")
                self.progress_event.emit(ProgressEvent("KASKÁDA", completed=idx, total=total, unit="kroků", detail=f"Krok {idx} dokončen."))

            result = {
                "mode": "KASKADA",
                "run_id": run_id,
                "response_id": last_response_id,
                "step_response_ids": per_step_response_ids,
                "step_json_outputs": per_step_json,
                "step_text_outputs": per_step_text,
                "text": per_step_text.get(str(total), ""),
                "step_out_files": per_step_out_files,
            }
            self.logger.update_state({
                "status": "completed",
                "finished_at": time.time(),
                "last_response_id": last_response_id,
                "steps_done": len(self.cfg.cascade.steps or []),
                "result": {
                    "step_response_ids": per_step_response_ids,
                    "step_json_outputs": per_step_json,
                    "step_out_files": per_step_out_files,
                },
            })
            self.logger.event("cascade.completed", result)
            self.finished_ok.emit(result)
        except Exception as ex:
            msg = str(ex)
            if self.logger:
                self.logger.event("cascade.failed", {"error": msg})
                self.logger.update_state({"status": "failed", "finished_at": time.time(), "error": msg})
            self.finished_err.emit(msg)
