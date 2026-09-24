from __future__ import annotations

import copy
import re
from typing import Any

SAFE_UI_FIELDS = (
    "project", "prompt", "mode", "send_as_c", "model", "model_a1", "model_a2",
    "model_a3", "response_id", "attached_file_ids", "input_file_ids",
    "attached_vector_store_ids", "in_dir", "out_dir", "in_equals_out", "versing",
    "temperature", "use_file_search", "diag_windows_in", "diag_windows_out",
    "diag_ssh_in", "diag_ssh_out", "ssh_user", "ssh_host", "ssh_key", "skip_paths",
    "skip_exts", "model_caps", "resume_files", "resume_prev_id", "ssh_pin",
    "ssh_pin_required", "caps_by_model", "available_models", "maximum_quality",
    "stop_after_plan", "dry_run", "auto_repair", "verification_profile_ids",
    "execution_approval_id", "qfile_output_path", "qfile_output_format",
    "qfile_suggest_path", "qfile_plan", "qa_continue_conversation",
    "preparation_snapshot", "completed_hashes", "recovery_instruction",
    "source_checkpoint_id",
)

_SECRET_KEYS = {
    "authorization", "proxy-authorization", "api_key", "apikey", "openai_api_key",
    "password", "passwd", "ssh_password", "secret", "token", "access_token",
    "refresh_token", "client_secret",
}
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]{8,}")
_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def safe_ui_state(cfg: Any) -> dict[str, Any]:
    """Explicitní whitelist persistovatelné konfigurace; credentials jsou runtime-only."""
    state: dict[str, Any] = {}
    for name in SAFE_UI_FIELDS:
        if hasattr(cfg, name):
            state[name] = copy.deepcopy(getattr(cfg, name))
    state["runtime_credentials"] = {"ssh_password": "runtime-only"}
    return state


def _redact_string(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _OPENAI_KEY.sub("[REDACTED_OPENAI_KEY]", value)
    return _KEY_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


def redact_evidence(value: Any) -> Any:
    """Druhá obranná vrstva pro logy, HTTP diagnostiku a odvozené exporty."""
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            name = str(key).strip().casefold().replace("-", "_")
            if name in {item.replace("-", "_") for item in _SECRET_KEYS}:
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_evidence(item)
        return result
    if isinstance(value, list):
        return [redact_evidence(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_evidence(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


# Kanonická data jsou již vybraná vstupní politikou a typovaným konfigurátorem.
# Diagnostická redakce nesmí pozměnit jejich obsah až po výpočtu hashů.
_CANONICAL_FIELDS = frozenset({
    "payload", "schema", "file_context", "projection", "source_snapshot",
    "preparation_snapshot", "graph", "plan", "requirements", "response",
    "prompt", "instructions", "input", "output", "output_text", "content",
    "text", "recovery_instruction", "repair_instruction",
    "target_id", "target_path", "path", "snapshot", "values", "legacy_context",
    "generate_batch", "generate_batches", "cascade_runtime",
})
_DIAGNOSTIC_FIELDS = frozenset({
    "error", "failure_detail", "last_error", "trace", "exception", "headers", "http_headers",
})


def _looks_like_json_schema(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if "$schema" in value or "$ref" in value or "$defs" in value:
        return True
    kind = value.get("type")
    return isinstance(kind, str) and kind in {
        "object", "array", "string", "number", "integer", "boolean", "null"
    } and any(
        key in value
        for key in (
            "properties", "items", "required", "additionalProperties",
            "enum", "anyOf",
        )
    )


def persist_evidence(value: Any) -> Any:
    """Bezpečný metadatový zápis, který zachová kanonické kontrakty bitově.

    Runtime credentials nepatří do provider payloadu ani do schémat. ui_state
    se vždy znovu promítá explicitním whitelistem; diagnostika používá oddělenou
    ztrátovou redakci. Odvozené historické exporty zůstávají redigované.
    """
    if isinstance(value, dict):
        if _looks_like_json_schema(value):
            return copy.deepcopy(value)
        if {"order_hash", "attempt_id", "input_projection_hash", "provider_endpoint"} <= value.keys():
            from .orchestration.work_order import WORK_ORDER_V2_SCHEMA, WORK_ORDER_V3_SCHEMA
            schema = WORK_ORDER_V3_SCHEMA if value.get("version") == 3 else WORK_ORDER_V2_SCHEMA
            if (set(schema["required"]) <= value.keys()
                    and value.keys() <= set(schema["properties"]) | {"order_hash"}):
                return copy.deepcopy(value)
        result = {}
        for key, item in value.items():
            name = str(key).strip().casefold().replace("-", "_")
            if name == "ui_state" and isinstance(item, dict):
                result[key] = {field: copy.deepcopy(item[field]) for field in SAFE_UI_FIELDS if field in item}
                result[key]["runtime_credentials"] = {"ssh_password": "runtime-only"}
            elif name in {secret.replace("-", "_") for secret in _SECRET_KEYS}:
                result[key] = "[REDACTED]"
            elif name in _DIAGNOSTIC_FIELDS:
                result[key] = redact_evidence(item)
            elif name in _CANONICAL_FIELDS:
                if name == "payload" and isinstance(item, dict):
                    forbidden = {str(k).casefold().replace("-", "_") for k in item} & {"authorization", "api_key", "ssh_password", "password", "headers"}
                    if forbidden:
                        raise ValueError("Runtime credentials/HTTP hlavičky nesmějí být v kanonickém payloadu.")
                result[key] = copy.deepcopy(item)
            else:
                result[key] = persist_evidence(item)
        return result
    if isinstance(value, list):
        return [persist_evidence(item) for item in value]
    if isinstance(value, tuple):
        return tuple(persist_evidence(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value
