"""Sdílené lokální podmínky požadavků Responses a atributů file search."""

from __future__ import annotations

import math
import re
from typing import Any


def uses_reasoning_defaults(model: str) -> bool:
    return bool(re.match(r"^(?:gpt-[56](?:[.-]|$)|o[134](?:-|$))", model))


def is_non_response_model(model: str) -> bool:
    return model.startswith(("dall-e", "gpt-image", "sora", "whisper", "tts-", "text-embedding", "omni-moderation")) or any(
        marker in model for marker in ("realtime", "transcribe", "-audio", "-tts")
    )


def validate_response_payload(payload: dict[str, Any]) -> None:
    allowed = {"model", "input", "instructions", "previous_response_id", "conversation", "text", "reasoning",
               "temperature", "top_p", "max_output_tokens", "tools", "tool_choice", "stream", "background",
               "store", "metadata", "service_tier", "truncation", "parallel_tool_calls", "include", "user",
               "safety_identifier", "prompt_cache_key", "prompt_cache_retention", "max_tool_calls"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Požadavek obsahuje neznámé parametry.")
    for key in ("stream", "background", "store", "parallel_tool_calls"):
        if key in payload and type(payload[key]) is not bool:
            raise ValueError(f"{key} musí být boolean.")
    if "instructions" in payload and not isinstance(payload["instructions"], str):
        raise ValueError("instructions musí být text.")
    for key, values in (("service_tier", ("auto", "default", "flex", "priority")), ("truncation", ("auto", "disabled")),
                        ("prompt_cache_retention", ("in-memory", "24h"))):
        if key in payload and payload[key] not in values:
            raise ValueError(f"Neplatné nastavení {key}.")
    for key in ("previous_response_id", "user", "safety_identifier", "prompt_cache_key"):
        if key in payload and (not isinstance(payload[key], str) or not payload[key].strip()):
            raise ValueError(f"{key} musí být neprázdný text.")
    if "metadata" in payload:
        metadata = payload["metadata"]
        if not isinstance(metadata, dict) or len(metadata) > 16 or any(
            not isinstance(k, str) or len(k) > 64 or not isinstance(v, str) or len(v) > 512 for k, v in metadata.items()
        ):
            raise ValueError("Neplatná metadata požadavku.")
    if "text" in payload:
        from .structured_output import prepare_payload
        prepare_payload(payload)
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("tools musí být seznam.")
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "file_search":
            raise ValueError("Program podporuje pouze ověřený nástroj file_search.")
        if set(tool) - {"type", "vector_store_ids", "max_num_results"}:
            raise ValueError("Neznámé nastavení file_search.")
        ids = tool.get("vector_store_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]+", v) for v in ids):
            raise ValueError("file_search vyžaduje platná vector_store_ids.")
        if "max_num_results" in tool and (type(tool["max_num_results"]) is not int or not 1 <= tool["max_num_results"] <= 50):
            raise ValueError("max_num_results musí být celé číslo od 1 do 50.")
    if "tool_choice" in payload and payload["tool_choice"] not in ("auto", "none", "required", {"type": "file_search"}):
        raise ValueError("Nepodporovaná volba nástroje.")
    if payload.get("tool_choice") not in (None, "none", "auto") and not tools:
        raise ValueError("Vynucení nástroje vyžaduje připojený nástroj.")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Požadavek vyžaduje model.")
    if is_non_response_model(model):
        raise ValueError("Vybraný model používá jiný endpoint než textové Responses.")
    if payload.get("previous_response_id") and payload.get("conversation"):
        raise ValueError("conversation a previous_response_id nelze kombinovat.")
    if payload.get("stream") or payload.get("background"):
        raise ValueError("Synchronní klient vyžaduje stream=false a background=false.")
    reasoning = payload.get("reasoning")
    if reasoning is None:
        reasoning = {}
    if not isinstance(reasoning, dict):
        raise ValueError("reasoning musí být objekt.")
    if set(reasoning) - {"effort", "summary"}:
        raise ValueError("Neznámé nastavení reasoning.")
    if "effort" in reasoning and reasoning["effort"] not in ("none", "minimal", "low", "medium", "high", "xhigh"):
        raise ValueError("Neplatné reasoning.effort.")
    if "summary" in reasoning and reasoning["summary"] not in ("auto", "concise", "detailed"):
        raise ValueError("Neplatné reasoning.summary.")
    if reasoning and model.startswith(("gpt-4.1", "gpt-4o")):
        raise ValueError("Tento model nepodporuje parametr reasoning.")
    for key, upper in (("temperature", 2), ("top_p", 1)):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= upper:
            raise ValueError(f"{key} musí být konečné číslo od 0 do {upper}.")
        if uses_reasoning_defaults(model):
            supports_none_sampling = bool(re.match(r"^gpt-5\.(?:1|2|4)(?:-|$)", model)) and not any(
                marker in model for marker in ("-pro", "-codex", "-chat")
            )
            if not supports_none_sampling or reasoning.get("effort") != "none":
                raise ValueError(f"{model}: {key} není povoleno v tomto režimu reasoning.")
    if model.startswith("gpt-6") and reasoning.get("effort") in ("none", "minimal"):
        raise ValueError("GPT-6 nepodporuje reasoning none ani minimal.")
    tokens = payload.get("max_output_tokens")
    if tokens is not None and (type(tokens) is not int or tokens < 16):
        raise ValueError("max_output_tokens musí být celé číslo nejméně 16.")
    messages = payload.get("input")
    if messages is not None and not isinstance(messages, (str, list)):
        raise ValueError("input musí být text nebo seznam vstupních položek.")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("Vstupní položka musí být objekt.")
            if message.get("type", "message") != "message":
                raise ValueError("Tento program přijímá pouze vstupní zprávy.")
            if message.get("role") not in ("user", "assistant", "system", "developer"):
                raise ValueError("Neplatná role vstupní zprávy.")
            content = message.get("content")
            if isinstance(content, str):
                continue
            if not isinstance(content, list):
                raise ValueError("Obsah zprávy musí být text nebo seznam částí.")
            for part in content:
                validate_input_part(part)


def validate_input_part(part):
    if not isinstance(part, dict):
        raise ValueError("Část vstupu musí být objekt.")
    kind = part.get("type")
    if kind == "input_text":
        if set(part) - {"type", "text"} or not isinstance(part.get("text"), str):
            raise ValueError("input_text vyžaduje text.")
    elif kind in ("input_file", "input_image"):
        sources = ("file_id", "file_data", "file_url") if kind == "input_file" else ("file_id", "image_url")
        if set(part) - {"type", *sources, "filename" if kind == "input_file" else "detail"}:
            raise ValueError("Neznámé parametry vstupní přílohy.")
        chosen = [key for key in sources if part.get(key) is not None]
        if len(chosen) != 1 or not isinstance(part[chosen[0]], str) or not part[chosen[0]].strip():
            raise ValueError(f"{kind} vyžaduje právě jeden neprázdný zdroj.")
        if "file_data" in chosen and not isinstance(part.get("filename"), str):
            raise ValueError("Vložený soubor vyžaduje filename.")
        if "file_id" in chosen and not re.fullmatch(r"[A-Za-z0-9_-]+", part["file_id"]):
            raise ValueError("Neplatný identifikátor vstupního souboru.")
        if "file_url" in chosen and not part["file_url"].startswith(("https://", "http://")):
            raise ValueError("Soubor vyžaduje HTTP(S) URL.")
        if "image_url" in chosen and not part["image_url"].startswith(("https://", "http://", "data:image/")):
            raise ValueError("Obrázek vyžaduje HTTP(S) URL nebo data:image.")
        if "detail" in part and part["detail"] not in ("auto", "low", "high", "original"):
            raise ValueError("Neplatná podrobnost vstupního obrázku.")
    else:
        raise ValueError("Nepodporovaný typ části vstupní zprávy.")


def validate_vector_attributes(attributes: dict[str, Any]) -> None:
    if not isinstance(attributes, dict) or len(attributes) > 16:
        raise ValueError("Atributy musí být objekt s nejvýše 16 položkami.")
    for key, value in attributes.items():
        if not isinstance(key, str) or len(key) > 64:
            raise ValueError("Klíč atributu musí být text do 64 znaků.")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("Hodnota atributu musí být text, číslo nebo boolean.")
        if isinstance(value, str) and len(value) > 512:
            raise ValueError("Text atributu smí mít nejvýše 512 znaků.")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Číselný atribut musí být konečný.")


def validate_run_options(cfg, check_models=True) -> None:
    if cfg.mode not in ("GENERATE", "MODIFY", "QA", "QFILE"):
        raise ValueError("Neznámý režim běhu.")
    if not cfg.model.strip() or not cfg.prompt.strip():
        raise ValueError("Vyberte model a vyplňte zadání.")
    if is_non_response_model(cfg.model):
        raise ValueError("Vybraný model není určen pro textové Responses.")
    available_models = getattr(cfg, "available_models", None)

    def validate_selected_model(model: str, caps: dict[str, Any]) -> None:
        if not check_models:
            return
        if available_models is None:
            raise ValueError("Chybí ověřený katalog modelů pro tento běh.")
        if model not in set(available_models):
            raise ValueError(f"{model}: model není v aktuálním katalogu API.")
        if caps.get("ok_basic") is not True:
            raise ValueError(f"{model}: model nemá úspěšný aktuální probe.")

    validate_selected_model(cfg.model, cfg.model_caps or {})
    if cfg.mode == "GENERATE":
        for field in ("model_a1", "model_a2", "model_a3"):
            model = getattr(cfg, field, "") or cfg.model
            validate_response_payload({"model": model})
            caps = cfg.model_caps if model == cfg.model else (getattr(cfg, "caps_by_model", None) or {}).get(model, {})
            validate_selected_model(model, caps)
            if check_models and field != "model_a3":
                if caps.get("supports_previous_response_id") is False:
                    raise ValueError(f"{model}: živá příprava vyžaduje návaznost Responses.")
                if cfg.attached_vector_store_ids and not caps.get("supports_file_search"):
                    raise ValueError(f"{model}: nejprve ověřte podporu file search pro model přípravy.")
        if cfg.send_as_c:
            from .generate_batch import validate_batch_model
            validate_batch_model(getattr(cfg, "model_a3", "") or cfg.model)
            if getattr(cfg, "resume_files", None):
                raise ValueError("Pro hybridní Batch použijte nový A1/A2 nebo opakování v panelu BATCH.")
    if cfg.send_as_c:
        if cfg.mode not in ("GENERATE", "MODIFY"):
            raise ValueError("Batch souborového kontraktu podporuje pouze GENERATE a MODIFY.")
        if cfg.response_id and cfg.mode != "GENERATE":
            raise ValueError("Batch nepoužívá návaznost response_id; pole musí být prázdné.")
        if cfg.mode != "GENERATE" and (cfg.attached_vector_store_ids or cfg.diag_windows_in or cfg.diag_ssh_in):
            raise ValueError("Batch souborového kontraktu nepodporuje vector store ani diagnostiku IN.")
        if cfg.diag_windows_out or cfg.diag_ssh_out:
            raise ValueError("Batch nespouští následné opravy OUT.")
    if check_models and cfg.mode != "GENERATE" and cfg.attached_vector_store_ids and not cfg.model_caps.get("supports_file_search"):
        raise ValueError("Pro připojený vector store nejprve ověřte podporu file search u modelu.")
