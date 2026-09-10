"""Verzovaná lokální matice; síť ani runtime cache nemění schopnosti modelů."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json


@lru_cache(maxsize=1)
def _matrix():
    return json.loads(files("kajovo.core").joinpath("openai_model_matrix.json").read_text(encoding="utf-8"))


def matrix_version():
    return _matrix()["version"]


def model_ids():
    return tuple(_matrix()["models"])


def model_spec(model):
    spec = _matrix()["models"].get(model)
    if spec is None:
        raise ValueError(f"{model}: model není v pevné matici {matrix_version()}; doplňte ověřenou specifikaci.")
    return deepcopy(spec)


def selectable(model):
    try:
        spec = model_spec(model)
    except ValueError:
        return False
    return spec["responses"] and "structured_outputs" in spec["features"] and not spec["deprecated"]


def validate_model_parameters(payload, batch=False):
    model = payload["model"]
    spec = model_spec(model)

    def reject(param, reason):
        raise ValueError(f"{model} / {'BATCH' if batch else 'LIVE'} / {param}: {reason} (matice {matrix_version()}).")

    if not selectable(model):
        reject("model", "model nemá dokumentované aktivní Responses se strict JSON Schema")
    if batch and not spec["batch"]:
        reject("model", "model nepodporuje Batch")
    if batch and (payload.get("previous_response_id") or payload.get("conversation")):
        reject("previous_response_id/conversation", "aplikace vyžaduje nezávislé dávkové řádky")
    reasoning = payload.get("reasoning") or {}
    if reasoning and not spec["reasoning_supported"]:
        reject("reasoning", "model nemá reasoning")
    for key, choices in (("effort", spec["reasoning"]), ("summary", spec["reasoning_summaries"]),
                         ("mode", spec["reasoning_modes"])):
        if key in reasoning and reasoning[key] not in choices:
            reject("reasoning." + key, f"povolené hodnoty: {', '.join(choices) or 'parametr vynechat'}")
    for param in ("temperature", "top_p"):
        if param in payload and payload[param] is not None:
            if spec["sampling"] == "omit":
                reject(param, "matice nepovoluje sampling; parametr vynechte")
            if spec["sampling"] == "explicit_none" and reasoning.get("effort") != "none":
                reject(param, "vyžaduje explicitní reasoning.effort=none")
    if payload.get("max_output_tokens", 16) > spec["max_output_tokens"]:
        reject("max_output_tokens", f"maximum je {spec['max_output_tokens']}")
    for tool in payload.get("tools", []):
        if tool["type"] not in spec["features"]:
            reject("tools." + tool["type"], "model nástroj nepodporuje")
    if "prompt_cache_retention" in payload and payload["prompt_cache_retention"] not in spec["cache_retention"]:
        reject("prompt_cache_retention", f"povolené hodnoty: {spec['cache_retention'] or 'parametr vynechat'}")
    if "prompt_cache_options" in payload and not spec["cache_options"]:
        reject("prompt_cache_options", "vyžaduje model s novým řízením cache")
    if payload.get("service_tier", "auto") not in spec["service_tiers"]:
        reject("service_tier", f"doložené hodnoty: {spec['service_tiers']}")
    if "reasoning.encrypted_content" in payload.get("include", []) and not spec["reasoning_supported"]:
        reject("include", "reasoning.encrypted_content vyžaduje reasoning model")
    for message in payload.get("input", []) if isinstance(payload.get("input"), list) else []:
        parts = message.get("content", [])
        for part in parts if isinstance(parts, list) else []:
            required = {"input_image": "image_input", "input_file": "file_uploads"}.get(part.get("type"))
            if required and required not in spec["features"]:
                reject("input." + part["type"], "model nepodporuje tento vstup")
            if part.get("type") == "input_file":
                from urllib.parse import urlsplit
                filename = part.get("filename") or urlsplit(part.get("file_url", "")).path
                is_pdf = filename.lower().endswith(".pdf") or part.get("file_data", "").startswith("data:application/pdf;")
                if is_pdf and "image_input" not in spec["features"]:
                    reject("input_file", "PDF vyžaduje vision model")
            if part.get("type") == "input_image" and part.get("detail", "auto") not in spec["image_details"]:
                reject("input_image.detail", f"povolené hodnoty: {spec['image_details']}")
    # Dražší režimy potřebují také účtové/regionální ověření, nejsou implicitně přenosné do Batch.
    if batch and payload.get("service_tier") not in (None, "auto", "default"):
        reject("service_tier", "aplikace v Batch nepovoluje live priority/flex")
    return spec
