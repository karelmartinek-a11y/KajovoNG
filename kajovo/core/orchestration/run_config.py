from __future__ import annotations

import hashlib
import json
from typing import Any

import jsonschema

DEFAULT_MAX_COST_MICROUSD = 25_000_000
DEFAULT_MAX_INPUT_TOKENS = 2_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 500_000
DEFAULT_MAX_PAID_REQUESTS = 200

RUN_CONFIG_V2_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "version": {"type": "integer", "enum": [2]},
        "workflow": {"type": "string", "enum": ["GENERATE", "MODIFY", "QA", "QFILE", "CASCADE", "PHOTO", "COMIC"]},
        "execution": {"type": "string", "enum": ["live", "batch"]},
        "quality": {"type": "string", "enum": ["standard", "maximum"]},
        "model_bindings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"stage": {"type": "string"}, "model": {"type": "string"}},
                "required": ["stage", "model"],
                "additionalProperties": False,
            },
        },
        "max_cost_microusd": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "max_input_tokens": {"type": "integer"},
        "max_output_tokens": {"type": "integer"},
        "max_paid_requests": {"type": "integer"},
        "unknown_pricing": {"type": "string", "enum": ["block", "explicit_token_budget"]},
        "auto_repair": {"type": "string", "enum": ["off", "within_approval"]},
        "verification_profile_ids": {"type": "array", "items": {"type": "string"}},
        "stop_after_plan": {"type": "boolean"},
        "dry_run": {"type": "boolean"},
    },
    "required": [
        "version", "workflow", "execution", "quality", "model_bindings",
        "max_cost_microusd", "max_input_tokens", "max_output_tokens",
        "max_paid_requests", "unknown_pricing", "auto_repair",
        "verification_profile_ids", "stop_after_plan", "dry_run",
    ],
    "additionalProperties": False,
}


def _model_bindings(cfg: Any) -> list[dict[str, str]]:
    mode = str(cfg.mode)
    if mode == "GENERATE":
        stage_models = [
            ("A0R", cfg.model_a1 or cfg.model),
            ("A1", cfg.model_a1 or cfg.model),
            ("A2", cfg.model_a2 or cfg.model),
            ("A2Q", cfg.model_a2 or cfg.model),
            ("A3", cfg.model_a3 or cfg.model),
        ]
    elif mode == "MODIFY":
        stage_models = [(stage, cfg.model) for stage in ("B0R", "B1", "B2", "B2Q", "B3")]
    else:
        stage_models = [(mode, cfg.model)]
    return [{"stage": stage, "model": str(model)} for stage, model in stage_models if model]


def build_run_config_v2(cfg: Any) -> dict[str, Any]:
    profiles = getattr(cfg, "verification_profile_ids", None)
    value = {
        "version": 2,
        "workflow": str(cfg.mode),
        "execution": "batch" if bool(getattr(cfg, "send_as_c", False)) else "live",
        "quality": "maximum" if bool(getattr(cfg, "maximum_quality", False)) else "standard",
        "model_bindings": _model_bindings(cfg),
        "max_cost_microusd": getattr(cfg, "max_cost_microusd", DEFAULT_MAX_COST_MICROUSD),
        "max_input_tokens": int(getattr(cfg, "max_input_tokens", DEFAULT_MAX_INPUT_TOKENS)),
        "max_output_tokens": int(getattr(cfg, "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)),
        "max_paid_requests": int(getattr(cfg, "max_paid_requests", DEFAULT_MAX_PAID_REQUESTS)),
        "unknown_pricing": str(getattr(cfg, "unknown_pricing", "block")),
        "auto_repair": str(getattr(cfg, "auto_repair", "off")),
        "verification_profile_ids": list(profiles or []),
        "stop_after_plan": bool(getattr(cfg, "stop_after_plan", False)),
        "dry_run": bool(getattr(cfg, "dry_run", False)),
    }
    validate_run_config_v2(value)
    return value


def validate_run_config_v2(value: dict[str, Any]) -> None:
    try:
        jsonschema.validate(value, RUN_CONFIG_V2_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"RUN_CONFIG_V2: {exc.message}") from exc
    if value["workflow"] not in {"GENERATE", "MODIFY"} and value["stop_after_plan"]:
        raise ValueError("stop_after_plan lze použít pouze pro GENERATE nebo MODIFY.")
    if value["workflow"] != "MODIFY" and value["dry_run"]:
        raise ValueError("dry_run lze použít pouze pro MODIFY.")
    for key in ("max_input_tokens", "max_output_tokens", "max_paid_requests"):
        if value[key] <= 0:
            raise ValueError(f"RUN_CONFIG_V2: {key} musí být kladné celé číslo.")
    if value["max_cost_microusd"] is not None and value["max_cost_microusd"] <= 0:
        raise ValueError("RUN_CONFIG_V2: max_cost_microusd musí být kladné nebo null.")


def run_scope_hash(cfg: Any) -> str:
    run_config = build_run_config_v2(cfg)
    scope = {
        "run_config_v2": run_config,
        "project": str(getattr(cfg, "project", "")),
        "prompt": str(getattr(cfg, "prompt", "")),
        "in_dir": str(getattr(cfg, "in_dir", "")),
        "out_dir": str(getattr(cfg, "out_dir", "")),
        "attached_file_ids": list(getattr(cfg, "attached_file_ids", []) or []),
        "input_file_ids": list(getattr(cfg, "input_file_ids", []) or []),
        "attached_vector_store_ids": list(getattr(cfg, "attached_vector_store_ids", []) or []),
    }
    raw = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def require_resumable_run_config_v2(ui_state: dict[str, Any], mode: str) -> None:
    if mode not in {"GENERATE", "MODIFY"}:
        return
    for key in ("stop_after_plan", "dry_run"):
        if key not in ui_state or type(ui_state[key]) is not bool:
            raise ValueError(
                "Historický běh nemá doloženou hodnotu "
                f"{key}; automatické pokračování je z bezpečnostních důvodů zablokováno."
            )
