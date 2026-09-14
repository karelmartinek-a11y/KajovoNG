"""Lokální měření a verzovaná politika; žádné generativní měřicí volání."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .context_compiler import canonical, content_hash
from .contracts import ContractError
from .model_registry import model_spec
from .context_pricing import PRICES, projected_cost


def measure(value):
    text = value if isinstance(value, str) else canonical(value)
    size = len(text.encode("utf-8"))
    return {"characters": len(text), "utf8_bytes": size,
            "estimated_tokens": math.ceil(size / 3), "token_upper_bound": size,
            "method": "utf8_bytes_div_3_estimate_v1"}


@dataclass(frozen=True)
class BudgetPolicy:
    soft_input_tokens: int = 80000
    hard_input_tokens: int = 200000
    justification_threshold: int = 150000
    safety_fraction: float = 0.10
    long_context_threshold: int | None = None

    def __post_init__(self):
        if not 0 <= self.safety_fraction < 1 or not 0 < self.soft_input_tokens <= self.hard_input_tokens:
            raise ValueError("Neplatná politika kontextového rozpočtu.")
        if self.long_context_threshold is not None and self.long_context_threshold <= 0:
            raise ValueError("Neplatný cenový práh.")


def classify(context):
    """Transparentní body podle kontraktu; přípona souboru se nepoužívá."""
    contract = context["implementation_contract"]
    facets = {f["kind"] for f in contract["facets"]}
    reasons = []
    risk = facets & {"security", "auth", "transactions", "concurrency", "locking", "persistence"}
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
    return {"version": 1, "score": score, "class": level,
            "reasoning_target": {"simple": "medium", "normal": "high", "complex": "xhigh"}[level],
            "soft_input_tokens": {"simple": 40000, "normal": 80000, "complex": 120000}[level],
            "reasons": reasons + [f"{len(context['dependency_contracts'])} závislostí, "
                f"{len(contract['interface_bindings'])} symbolů, {len(contract['test_scenarios'])} scénářů",
                f"Očekávaný viditelný výstup {expected} tokenů"]}


def configure_file_request(payload, compiled, *, maximum_quality=False):
    """Zachová zvolený model; nedostatečná kapacita vyžaduje rozhodnutí přípravy."""
    routing = classify(compiled["working_context"])
    spec = model_spec(payload["model"])
    order = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    supported = spec["reasoning"]
    if spec["reasoning_supported"] and supported:
        target = order.index(routing["reasoning_target"])
        candidates = [e for e in supported if order.index(e) >= target]
        effort = max(supported, key=order.index) if maximum_quality else (
            min(candidates, key=order.index) if candidates else max(supported, key=order.index))
        payload["reasoning"] = {"effort": effort}
        reserve = {"none": 0, "minimal": 2048, "low": 4096, "medium": 8192,
                   "high": 16384, "xhigh": 32768, "max": 49152}[effort]
    else:
        reserve = 0
    expected = compiled["working_context"]["implementation_contract"]["expected_output_tokens"]
    output = math.ceil(expected * 1.5) + reserve + 1024
    if not spec["max_output_tokens"] or output > spec["max_output_tokens"]:
        raise ContractError("Očekávaný úplný výstup s reasoning rezervou se nevejde; rozdělte odpovědnost nebo zvolte kapacitní model.")
    payload["max_output_tokens"] = max(1024, output)
    payload["truncation"] = "disabled"
    if spec["sampling"] == "omit" or (spec["sampling"] == "explicit_none" and
                                       payload.get("reasoning", {}).get("effort") != "none"):
        payload.pop("temperature", None)
        payload.pop("top_p", None)
    routing.update(model=payload["model"], output_budget=payload["max_output_tokens"],
                   model_reason="Zachována explicitní volba uživatele; žádný automatický downgrade")
    return routing


def measure_request(payload, *, policy=None, exact_input_tokens=None, justification="", compiled=None, batch=False):
    policy = policy or BudgetPolicy(
        soft_input_tokens=classify(compiled["working_context"])["soft_input_tokens"] if compiled else 80000,
        long_context_threshold=PRICES.get(payload["model"], {}).get("long_context_threshold"))
    spec = model_spec(payload["model"])
    breakdown = {k: measure(payload.get(k, "")) for k in ("instructions", "input", "text", "tools")}
    context = compiled["working_context"] if compiled else None
    if context:
        components = {k: measure(v) for k, v in context.items()}
    else:
        components = {}
    unresolved = []
    if payload.get("previous_response_id"):
        unresolved.append("serverová historie")
    if payload.get("tools"):
        unresolved.append("budoucí retrieval")
    if isinstance(payload.get("input"), list):
        for message in payload["input"]:
            for part in message.get("content", []) if isinstance(message, dict) else []:
                if isinstance(part, dict) and part.get("type") in ("input_file", "input_image"):
                    unresolved.append(part["type"])
    estimated = sum(v["estimated_tokens"] for v in breakdown.values()) + 32
    upper = sum(v["token_upper_bound"] for v in breakdown.values()) + 128
    if exact_input_tokens is not None and (type(exact_input_tokens) is not int or exact_input_tokens < 0):
        raise ValueError("Přesný počet tokenů musí být nezáporné celé číslo.")
    count = exact_input_tokens if exact_input_tokens is not None else estimated
    safety_count = exact_input_tokens if exact_input_tokens is not None else upper
    window = spec["context_window"]
    margin = math.ceil(window * policy.safety_fraction) if window else None
    output = payload.get("max_output_tokens")
    warnings, blockers = [], []
    if count > policy.soft_input_tokens:
        warnings.append("Vstup překračuje měkký provozní limit.")
    if count > policy.hard_input_tokens:
        blockers.append("Vstup překračuje pevný provozní limit.")
    if count > policy.justification_threshold and not justification.strip():
        blockers.append("Velký vstup vyžaduje explicitní zdůvodnění.")
    long_context = policy.long_context_threshold is not None and count > policy.long_context_threshold
    if long_context and not justification.strip():
        blockers.append("Překročení doloženého cenového prahu vyžaduje zdůvodnění.")
    if not window or output is None:
        warnings.append("Neznámé kontextové okno nebo nerezervovaný výstup.")
    elif safety_count + output + margin > window:
        blockers.append("Vstup, výstup a bezpečnostní rezerva překračují kontext modelu; použijte přesné ne-generativní měření nebo upravte přípravu.")
    if spec.get("max_input_tokens") and safety_count > spec["max_input_tokens"]:
        blockers.append("Překročen samostatný vstupní limit modelu.")
    if unresolved and exact_input_tokens is None:
        warnings.append("Měření neobsahuje: " + ", ".join(sorted(set(unresolved))))
    return {"version": 1, "request_hash": content_hash(payload), "model": payload["model"],
            "reasoning": payload.get("reasoning"), "input_tokens": count,
            "input_tokens_exact": exact_input_tokens is not None,
            "input_token_upper_bound": upper if exact_input_tokens is None else exact_input_tokens,
            "output_budget": output, "context_window": window, "safety_margin": margin,
            "context_fraction": count / window if window else None,
            "context_breakdown": breakdown, "component_breakdown": components,
            "unknown_components": sorted(set(unresolved)),
            "long_context_pricing_threshold": policy.long_context_threshold,
            "long_context_exceeded": long_context if policy.long_context_threshold else None,
            "warnings": warnings, "blockers": blockers, "justification": justification,
            "file_context_hash": compiled.get("file_context_hash") if compiled else None,
            "dependency_count": len(context["dependency_contracts"]) if context else None,
            "projected_cost": projected_cost(payload["model"], count, output, batch=batch), "actual_cost": None,
            "pricing_status": "Doložená základní sazba; ne účetní cena." if payload["model"] in PRICES else "Cena není doložena verzovaným ceníkem; nevymýšlí se.",
            "status": "blocked" if blockers else "measured", "actual_usage": None,
            "incomplete_reason": None, "retry_count": 0}


def enforce_budget(report):
    if report["blockers"]:
        error = ContractError(" ".join(report["blockers"]))
        error.context_report = report
        raise error
    return report


def checked_measurement(payload, client, **kwargs):
    """Při nejisté kapacitě změří skutečný vstup bez generativního požadavku."""
    report = measure_request(payload, **kwargs)
    unresolved = set(report["unknown_components"]) - {"budoucí retrieval"}
    if report["blockers"] or unresolved:
        try:
            measured = client.count_input_tokens(payload)
            if (not isinstance(measured, dict)
                    or measured.get("request_hash") != content_hash(payload)
                    or type(measured.get("input_tokens")) is not int
                    or measured["input_tokens"] < 0):
                raise ValueError("Měření tokenů není platné pro tento požadavek.")
        except Exception as exc:
            report["blockers"].append("Kapacitu vstupu nelze bezpečně ověřit; ne-generativní měření tokenů selhalo.")
            report["status"] = "blocked"
            error = ContractError(report["blockers"][-1])
            error.context_report = report
            raise error from exc
        report = measure_request(payload, exact_input_tokens=measured["input_tokens"], **kwargs)
    return enforce_budget(report)


def preparation_measurement(payload, client):
    """Globální příprava má rozpočet modelu; limit jednoho souboru se nepřenáší."""
    spec = model_spec(payload["model"])
    window, output = spec["context_window"], spec["max_output_tokens"]
    if not window or not output:
        raise ContractError("Pro přípravu chybí doložená kapacita zvoleného modelu.")
    payload["max_output_tokens"] = output
    payload["truncation"] = "disabled"
    hard = min(window - math.ceil(window * 0.10) - output, spec.get("max_input_tokens") or window)
    if hard <= 0:
        raise ContractError("Výstup a bezpečnostní rezerva vyčerpávají kapacitu modelu.")
    return checked_measurement(payload, client, policy=BudgetPolicy(
        soft_input_tokens=min(80000, hard), hard_input_tokens=hard,
        long_context_threshold=PRICES.get(payload["model"], {}).get("long_context_threshold")),
        justification="Globální příprava vyžaduje úplné zadání a kanonické podklady; bez opakované historie.")
