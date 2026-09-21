"""Přísné JSON hranice pro nové odpovědi; historické čtečky zůstávají oddělené."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .errors import OrchestrationError


def canonical_bytes(value: Any) -> bytes:
    """Kanonizace dle CHANGE/reference/invariants.py bez změny Unicode a řádků."""
    def check(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise OrchestrationError("CANONICAL_KEY", "Klíč JSON musí být řetězec.")
                key.encode("utf-8", errors="strict")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif isinstance(item, str):
            item.encode("utf-8", errors="strict")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise OrchestrationError("NONFINITE", "JSON nesmí obsahovat nekonečné číslo.")
        elif item is not None and type(item) not in (int, bool):
            raise OrchestrationError("INVALID_JSON", "Hodnota není typem JSON.")

    try:
        check(value)
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8", errors="strict")
    except (UnicodeError, RecursionError) as exc:
        raise OrchestrationError("INVALID_JSON", "Neplatný Unicode nebo vnoření JSON.") from exc


def canonical_sha256(value: Any) -> str:
    """Hash objektu nepoužívá formátování ani implicitní default=str."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_json_value_strict(text: str) -> Any:
    """Přijme právě jednu JSON hodnotu bez duplicitních klíčů a non-finite čísel."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise OrchestrationError("DUPLICATE_KEY", "Duplicitní klíč JSON.")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise OrchestrationError("NONFINITE", "JSON nesmí obsahovat " + value + ".")

    if not isinstance(text, str):
        raise OrchestrationError("INVALID_JSON", "Vstup JSON musí být řetězec.")
    try:
        parsed = json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)
    except OrchestrationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise OrchestrationError("INVALID_JSON", "Vstup není právě jedna úplná platná JSON hodnota.") from exc
    canonical_bytes(parsed)
    return parsed


def parse_json_strict(text: str) -> dict[str, Any]:
    """Přijme jediný objekt; nevyhledává JSON uvnitř prózy ani markdownu."""
    parsed = parse_json_value_strict(text)
    if not isinstance(parsed, dict):
        raise OrchestrationError("ROOT_NOT_OBJECT", "Kořen JSON musí být objekt.")
    return parsed
