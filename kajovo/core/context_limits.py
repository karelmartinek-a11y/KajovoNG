"""Technická validace kontextu a parametrů modelu bez finanční logiky."""
from __future__ import annotations

import math
from typing import Any

from .context_compiler import canonical, content_hash
from .contracts import ContractError
from .model_registry import model_spec, validate_model_parameters


def measure(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else canonical(value)
    size = len(text.encode("utf-8"))
    return {
        "characters": len(text),
        "utf8_bytes": size,
        "estimated_tokens": math.ceil(size / 3),
        "token_upper_bound": size,
        "method": "utf8_bytes_div_3_estimate_v1",
    }


def classify(context: dict[str, Any]) -> dict[str, Any]:
    """Určí technickou složitost požadavku pro volbu reasoning effort."""
    contract = context["implementation_contract"]
    facets = {f["kind"] for f in contract["facets"]}
    reasons: list[str] = []
    risk = facets & {
        "security", "auth", "transactions", "concurrency", "locking", "persistence"
    }
    score = len(context["dependency_contracts"]) + len(contract["interface_bindings"])
    score += 3 * len(risk) + len(contract["test_scenarios"])
    if risk:
        reasons.append("Citlivé oblasti: " + ", ".join(sorted(risk)))
    if context["cycle_contracts"]:
        score += 10
        reasons.append("Koordinované cyklické rozhraní")
    expected = contract["expected_output_tokens"]
    score += math.ceil(expected / 4000)
    level = "complex" if score >= 16 else "normal" if score >= 5 else "simple"
    if risk and level == "simple":
        level = "normal"
    return {
        "version": 1,
        "score": score,
        "class": level,
        "reasoning_target": {
            "simple": "medium", "normal": "high", "complex": "xhigh"
        }[level],
        "reasons": reasons + [
            f"{len(context['dependency_contracts'])} závislostí, "
            f"{len(contract['interface_bindings'])} symbolů, "
            f"{len(contract['test_scenarios'])} scénářů",
            f"Očekávaný viditelný výstup {expected} tokenů",
        ],
    }


def configure_file_request(
    payload: dict[str, Any],
    compiled: dict[str, Any],
    *,
    maximum_quality: bool = False,
) -> dict[str, Any]:
    """Nastaví výstup pouze podle technických capabilities vybraného modelu."""
    routing = classify(compiled["working_context"])
    spec = model_spec(payload["model"])
    order = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    supported = spec["reasoning"]
    if spec["reasoning_supported"] and supported:
        target = order.index(routing["reasoning_target"])
        candidates = [e for e in supported if order.index(e) >= target]
        effort = (
            max(supported, key=order.index)
            if maximum_quality
            else (min(candidates, key=order.index) if candidates else max(supported, key=order.index))
        )
        payload["reasoning"] = {"effort": effort}
        reasoning_reserve = {
            "none": 0,
            "minimal": 2048,
            "low": 4096,
            "medium": 8192,
            "high": 16384,
            "xhigh": 32768,
            "max": 49152,
        }[effort]
    else:
        reasoning_reserve = 0

    expected = compiled["working_context"]["implementation_contract"]["expected_output_tokens"]
    output_limit = math.ceil(expected * 1.5) + reasoning_reserve + 1024
    provider_max = int(spec.get("max_output_tokens") or 0)
    if not provider_max or output_limit > provider_max:
        raise ContractError(
            "Očekávaný úplný výstup se nevejde do technického výstupního limitu modelu; "
            "rozdělte odpovědnost nebo zvolte kapacitní model."
        )
    payload["max_output_tokens"] = max(1024, output_limit)
    payload["truncation"] = "disabled"
    if spec["sampling"] == "omit" or (
        spec["sampling"] == "explicit_none"
        and payload.get("reasoning", {}).get("effort") != "none"
    ):
        payload.pop("temperature", None)
        payload.pop("top_p", None)
    routing.update(
        model=payload["model"],
        output_limit=payload["max_output_tokens"],
        model_reason="Zachována explicitní volba uživatele; automatický downgrade se neprovádí",
    )
    return routing


def measure_request(
    payload: dict[str, Any],
    *,
    exact_input_tokens: int | None = None,
    compiled: dict[str, Any] | None = None,
    batch: bool = False,
) -> dict[str, Any]:
    """Vrátí technickou context-window evidenci; nepočítá ani neodhaduje cenu."""
    try:
        spec = validate_model_parameters(payload, batch=batch)
    except ValueError as exc:
        return {
            "version": 2,
            "request_hash": content_hash(payload),
            "model": str(payload.get("model") or ""),
            "input_tokens": 0,
            "input_tokens_exact": False,
            "input_token_upper_bound": 0,
            "output_limit": payload.get("max_output_tokens"),
            "context_window": None,
            "context_breakdown": {},
            "component_breakdown": {},
            "unknown_components": [],
            "warnings": [],
            "blockers": [str(exc)],
            "status": "blocked",
            "file_context_hash": compiled.get("file_context_hash") if compiled else None,
        }

    breakdown = {
        key: measure(payload.get(key, ""))
        for key in ("instructions", "input", "text", "tools")
    }
    context = compiled["working_context"] if compiled else None
    components = {key: measure(value) for key, value in context.items()} if context else {}

    unresolved: list[str] = []
    if payload.get("previous_response_id"):
        unresolved.append("serverová historie")
    if payload.get("tools"):
        unresolved.append("budoucí retrieval")
    if isinstance(payload.get("input"), list):
        for message in payload["input"]:
            for part in message.get("content", []) if isinstance(message, dict) else []:
                if isinstance(part, dict) and part.get("type") in ("input_file", "input_image"):
                    unresolved.append(part["type"])

    estimated = sum(value["estimated_tokens"] for value in breakdown.values()) + 32
    upper = sum(value["token_upper_bound"] for value in breakdown.values()) + 128
    if exact_input_tokens is not None and (
        type(exact_input_tokens) is not int or exact_input_tokens < 0
    ):
        raise ValueError("Přesný počet tokenů musí být nezáporné celé číslo.")

    count = exact_input_tokens if exact_input_tokens is not None else estimated
    fit_count = exact_input_tokens if exact_input_tokens is not None else upper
    window = int(spec.get("context_window") or 0)
    output = payload.get("max_output_tokens")
    output_value = int(output) if type(output) is int else 0
    warnings: list[str] = []
    blockers: list[str] = []

    if not window:
        blockers.append("Model nemá doložené technické context window.")
    elif fit_count + output_value > window:
        blockers.append(
            "Vstup a maximální výstup překračují technické context window modelu."
        )

    max_input = int(spec.get("max_input_tokens") or 0)
    if max_input and fit_count > max_input:
        blockers.append("Vstup překračuje samostatný technický vstupní limit modelu.")

    provider_output = int(spec.get("max_output_tokens") or 0)
    if output is None:
        blockers.append("Požadavek nemá explicitní max_output_tokens.")
    elif provider_output and output_value > provider_output:
        blockers.append("max_output_tokens překračuje technickou capability modelu.")

    if unresolved and exact_input_tokens is None:
        warnings.append(
            "Lokální odhad neobsahuje: " + ", ".join(sorted(set(unresolved)))
        )

    return {
        "version": 2,
        "request_hash": content_hash(payload),
        "model": payload["model"],
        "reasoning": payload.get("reasoning"),
        "input_tokens": count,
        "input_tokens_exact": exact_input_tokens is not None,
        "input_token_upper_bound": fit_count,
        "output_limit": output,
        "context_window": window or None,
        "context_fraction": (count / window) if window else None,
        "context_breakdown": breakdown,
        "component_breakdown": components,
        "unknown_components": sorted(set(unresolved)),
        "warnings": warnings,
        "blockers": blockers,
        "file_context_hash": compiled.get("file_context_hash") if compiled else None,
        "dependency_count": len(context["dependency_contracts"]) if context else None,
        "status": "blocked" if blockers else "measured",
    }


def ensure_technical_limits(report: dict[str, Any]) -> dict[str, Any]:
    if report["blockers"]:
        error = ContractError(" ".join(report["blockers"]))
        error.context_report = report
        raise error
    return report


def checked_measurement(payload: dict[str, Any], client, **kwargs: Any) -> dict[str, Any]:
    """Při neúplném lokálním odhadu použije ne-generativní provider token count."""
    report = measure_request(payload, **kwargs)
    unresolved = set(report["unknown_components"]) - {"budoucí retrieval"}
    if report["blockers"] or unresolved:
        try:
            measured = client.count_input_tokens(payload)
            if (
                not isinstance(measured, dict)
                or measured.get("request_hash") != content_hash(payload)
                or type(measured.get("input_tokens")) is not int
                or measured["input_tokens"] < 0
            ):
                raise ValueError("Měření tokenů není platné pro tento požadavek.")
        except Exception as exc:
            report["blockers"].append(
                "Technickou kapacitu vstupu nelze ověřit; ne-generativní měření tokenů selhalo."
            )
            report["status"] = "blocked"
            error = ContractError(report["blockers"][-1])
            error.context_report = report
            raise error from exc
        report = measure_request(
            payload,
            exact_input_tokens=measured["input_tokens"],
            **kwargs,
        )
    return ensure_technical_limits(report)


def preparation_measurement(payload: dict[str, Any], client) -> dict[str, Any]:
    """Příprava používá výhradně technické limity z capability registry."""
    spec = model_spec(payload["model"])
    window = int(spec.get("context_window") or 0)
    output = int(spec.get("max_output_tokens") or 0)
    if not window or not output:
        raise ContractError("Pro přípravu chybí doložená technická kapacita zvoleného modelu.")
    payload["max_output_tokens"] = output
    payload["truncation"] = "disabled"
    return checked_measurement(payload, client)
