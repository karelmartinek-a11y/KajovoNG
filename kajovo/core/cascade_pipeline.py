from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from .cascade_log import CascadeLogger
from .cascade_types import CascadeDefinition, CascadeStep
from .contracts import extract_text_from_response, parse_json_strict
from .openai_client import OpenAIClient
from .retry import CircuitBreaker, with_retry
from .utils import new_run_id


PLACEHOLDER_RE = re.compile(r"\{\{\s*step\.(\d+)\.(response_id|json)\s*\}\}")


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
                            "Podporované placeholdery: {{step.N.response_id}} a {{step.N.json}}. Pokud implementace podporuje out-file placeholdery, lze použít i {{step.N.out_file_id:REL_PATH}} a {{step.N.out_file_path:REL_PATH}}."
                        ),
                    },
                    "output_type": {"type": "string", "enum": ["text", "json"]},
                    "output_schema_kind": {
                        "type": ["string", "null"],
                        "enum": ["manifest", "prompts", "custom", None],
                    },
                    "output_schema_custom": {"type": ["object", "null"]},
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


class CascadeRunWorker(QThread):
    progress = Signal(int)
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
        self.cfg = cfg
        self.settings = settings
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
            if key == "response_id":
                return str(context.get(f"step.{idx}.response_id", ""))
            val = context.get(f"step.{idx}.json")
            if val is None:
                return ""
            if isinstance(val, str):
                return val
            return json.dumps(val, ensure_ascii=False)

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

    def _validate_json_output(self, obj: Dict[str, Any], schema: Dict[str, Any]) -> None:
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

    def run(self):
        run_id = new_run_id()
        self.logger = CascadeLogger(self.settings.log_dir, run_id, project_name=self.cfg.project)
        try:
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
            client = OpenAIClient(self.api_key)
            context: Dict[str, Any] = {}
            per_step_response_ids: Dict[str, str] = {}
            per_step_json: Dict[str, Any] = {}
            last_response_id = ""

            total = max(1, len(self.cfg.cascade.steps or []))
            for idx, raw_step in enumerate(self.cfg.cascade.steps or [], start=1):
                self._check_stop()
                step = CascadeStep.from_dict(raw_step.to_dict())
                step_label = step.title or f"Step {idx}"
                base_p = int((idx - 1) * 100 / total)
                self._emit_status(base_p, 0, f"Krok {idx}/{total}: {step_label}")
                self.logger.event("cascade.step.start", {"idx": idx, "title": step_label, "model": step.model})

                file_ids = list(step.files_existing_ids or [])
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

                resolved_instructions = self._resolve_text(step.instructions, context)
                resolved_input_text = self._resolve_text(step.input_text, context)
                resolved_prev_expr = self._resolve_text(step.previous_response_id_expr or "", context).strip()
                resolved_content_json = self._resolve_json(step.input_content_json, context) if step.input_content_json is not None else None

                if resolved_content_json is not None:
                    content_parts = self._normalize_content_parts(resolved_content_json, idx)
                else:
                    content_parts = [{"type": "input_text", "text": resolved_input_text}]

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
                if step.output_type == "json":
                    if schema is not None:
                        self._validate_schema_minimal(schema)
                        payload["text"] = {
                            "format": {
                                "type": "json_schema",
                                "name": f"cascade_step_{idx:02d}_schema",
                                "strict": True,
                                "schema": schema,
                            }
                        }
                    else:
                        payload["text"] = {"format": {"type": "json_object"}}

                    if step.output_schema_kind == "prompts":
                        input_messages.append(copy.deepcopy(PROMPTS_JSON_DEVELOPER_MESSAGE))
                    else:
                        input_messages.append(copy.deepcopy(JSON_ONLY_DEVELOPER_MESSAGE))

                input_messages.append({"type": "message", "role": "user", "content": content_parts})
                payload["input"] = input_messages

                self.logger.save_json("requests", f"cascade_step_{idx:02d}", payload)
                self._emit_status(base_p, 55, f"OpenAI request krok {idx}")
                response = with_retry(lambda p=payload: client.create_response(p), self.settings.retry, self.breaker)
                self.logger.save_json("responses", f"cascade_step_{idx:02d}", response)

                response_id = str(response.get("id") or "").strip()
                if response_id:
                    context[f"step.{idx}.response_id"] = response_id
                    per_step_response_ids[str(idx)] = response_id
                    last_response_id = response_id

                if step.output_type == "json":
                    text = extract_text_from_response(response)
                    parsed = parse_json_strict(text)
                    self._validate_json_output(parsed, schema or {})
                    context[f"step.{idx}.json"] = parsed
                    per_step_json[str(idx)] = parsed
                    self.logger.save_json("misc", f"cascade_step_{idx:02d}_json", parsed)

                self.logger.event(
                    "cascade.step.ok",
                    {
                        "idx": idx,
                        "title": step_label,
                        "response_id": response_id,
                        "json_output": bool(step.output_type == "json"),
                        "file_ids": file_ids,
                    },
                )
                self._emit_status(int(idx * 100 / total), 100, f"Krok {idx} dokončen")

            result = {
                "mode": "KASKADA",
                "run_id": run_id,
                "response_id": last_response_id,
                "step_response_ids": per_step_response_ids,
                "step_json_outputs": per_step_json,
            }
            self.logger.update_state({
                "status": "completed",
                "finished_at": time.time(),
                "last_response_id": last_response_id,
                "steps_done": len(self.cfg.cascade.steps or []),
                "result": {
                    "step_response_ids": per_step_response_ids,
                    "step_json_outputs": per_step_json,
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
