from __future__ import annotations

import json, re
from typing import Any, Dict, List
from .utils import validate_relative_path

class ContractError(Exception):
    pass


def validate_chunk_metadata(chunk: Any) -> None:
    """Ověří návaznost části; nulový počet znamená dosud neurčený celek."""
    if not isinstance(chunk, dict):
        raise ContractError("chunking musí být objekt.")
    index, count = chunk.get("chunk_index"), chunk.get("chunk_count", 0)
    if type(index) is not int or not 0 <= index <= 5000:
        raise ContractError("Neplatný index části souboru.")
    if type(count) is not int or not 0 <= count <= 5001:
        raise ContractError("Neplatný počet částí souboru.")
    more, following = chunk.get("has_more"), chunk.get("next_chunk_index")
    if type(more) is not bool:
        raise ContractError("has_more musí být boolean.")
    if count and (index >= count or more != (index < count - 1)):
        raise ContractError("Konec souboru neodpovídá deklarovanému počtu částí.")
    if more:
        if type(following) is not int or following != index + 1 or following > 5000:
            raise ContractError("Neplatný index následující části.")
    elif following is not None:
        raise ContractError("Poslední část nesmí odkazovat na pokračování.")

def extract_text_from_response(resp: Dict[str, Any]) -> str:
    if not isinstance(resp, dict):
        raise ContractError("Odpověď API musí být objekt.")
    if resp.get("status") not in (None, "completed"):
        raise ContractError("Odpověď API není dokončená; nelze použít částečný výstup.")
    if resp.get("error"):
        raise ContractError("API vrátilo chybu místo výsledku.")
    for item in resp.get("output", []) or []:
        if isinstance(item, dict):
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") == "refusal":
                    raise ContractError("Model odmítl požadavek.")
    if "output_text" in resp and isinstance(resp["output_text"], str):
        return resp["output_text"]
    out = resp.get("output")
    if isinstance(out, list):
        texts: List[str] = []
        for item in out:
            if isinstance(item, dict):
                content = item.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") in ("output_text","text"):
                            t = c.get("text") or c.get("content") or ""
                            if isinstance(t, str):
                                texts.append(t)
        if texts:
            return "\n".join(texts)
    for k in ("text","content","message"):
        if isinstance(resp.get(k), str):
            return resp[k]
    raise ContractError("Odpověď API neobsahuje textový výsledek.")

_JSON_OBJ_RE = re.compile(r"(\{.*\})", re.DOTALL)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicitní klíč JSON: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ContractError(f"Nepřípustná konstanta JSON: {value}")


def _load_json(text):
    def finite_float(value):
        import math
        number = float(value)
        if not math.isfinite(number):
            raise ContractError("Číslo JSON překračuje konečný rozsah.")
        return number
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant, parse_float=finite_float)


def structure_response_format(contract: str) -> Dict[str, Any]:
    """Schéma struktury souborů používané před generováním jejich obsahu."""
    modifying = contract == "B2_STRUCTURE"
    if contract not in ("A2_STRUCTURE", "B2_STRUCTURE"):
        raise ValueError("Neznámý strukturní kontrakt.")
    properties = {"path": {"type": "string"}}
    if modifying:
        properties.update(action={"type": "string", "enum": ["add", "modify"]}, intent={"type": "string"})
    else:
        properties["purpose"] = {"type": "string"}
    files_key = "touched_files" if modifying else "files"
    schema = {"type": "object", "properties": {
        "contract": {"type": "string", "enum": [contract]},
        files_key: {"type": "array", "items": {"type": "object", "properties": properties,
                    "required": list(properties), "additionalProperties": False}},
    }, "required": ["contract", files_key], "additionalProperties": False}
    return {"format": {"type": "json_schema", "name": contract, "strict": True, "schema": schema}}


def file_response_format(contract: str, path: str, chunk_index: int, action=None) -> Dict[str, Any]:
    properties = {
        "contract": {"type": "string", "enum": [contract]},
        "path": {"type": "string", **({"enum": [path]} if path is not None else {})},
        "content": {"type": "string"},
        "chunking": {"type": "object", "properties": {
            "chunk_index": {"type": "integer", "enum": [chunk_index]},
            "chunk_count": {"type": "integer", "minimum": 1},
            "has_more": {"type": "boolean"},
            "next_chunk_index": {"type": ["integer", "null"], "enum": [chunk_index + 1, None]},
        }, "required": ["chunk_index", "chunk_count", "has_more", "next_chunk_index"], "additionalProperties": False},
    }
    if action is not None:
        properties["action"] = {"type": "string", "enum": [action]}
    schema = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    return {"format": {"type": "json_schema", "name": contract, "strict": True, "schema": schema}}

def parse_json_strict(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        parsed = _load_json(text)
    except ContractError:
        raise
    except Exception:
        parsed = None

    if isinstance(parsed, dict):
        return parsed
    if parsed is not None:
        raise ContractError("Response JSON must be an object.")

    m = _JSON_OBJ_RE.search(text)
    if m:
        try:
            parsed2 = _load_json(m.group(1))
            if isinstance(parsed2, dict):
                return parsed2
        except Exception:
            pass
    raise ContractError("Response is not valid JSON (strict contract violated).")

def validate_paths(files: List[Dict[str, Any]]) -> None:
    if not isinstance(files, list):
        raise ContractError("files musí být seznam.")
    seen = set()
    for f in files:
        if not isinstance(f, dict):
            raise ContractError("Položka files[] musí být objekt.")
        p = f.get("path","")
        if not isinstance(p, str) or not p:
            raise ContractError("Invalid path in files[]")
        if p.startswith("/") or p.startswith("\\"):
            raise ContractError(f"Path must be relative: {p}")
        if ".." in p.split("/"):
            raise ContractError(f"Path cannot contain '..': {p}")
        if "\\" in p:
            raise ContractError(f"Path cannot contain \\: {p}")
        try:
            validate_relative_path(p)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        if p.casefold() in seen:
            raise ContractError(f"Duplicate path: {p}")
        seen.add(p.casefold())
    for path in seen:
        parts = path.split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise ContractError(f"Soubor koliduje s nadřazeným adresářem: {path}")
