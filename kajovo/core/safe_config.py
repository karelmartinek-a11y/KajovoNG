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
