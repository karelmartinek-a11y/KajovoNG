from __future__ import annotations

import json, re
from typing import Any, Dict, List

class ContractError(Exception):
    pass

def extract_text_from_response(resp: Dict[str, Any]) -> str:
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
    return json.dumps(resp, ensure_ascii=False)

_JSON_OBJ_RE = re.compile(r"(\{.*\})", re.DOTALL)

def parse_json_strict(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None

    if isinstance(parsed, dict):
        return parsed
    if parsed is not None:
        raise ContractError("Response JSON must be an object.")

    m = _JSON_OBJ_RE.search(text)
    if m:
        try:
            parsed2 = json.loads(m.group(1))
            if isinstance(parsed2, dict):
                return parsed2
        except Exception:
            pass
    raise ContractError("Response is not valid JSON (strict contract violated).")

def validate_paths(files: List[Dict[str, Any]]) -> None:
    seen = set()
    for f in files:
        p = f.get("path","")
        if not isinstance(p, str) or not p:
            raise ContractError("Invalid path in files[]")
        if p.startswith("/") or p.startswith("\\"):
            raise ContractError(f"Path must be relative: {p}")
        if ".." in p.split("/"):
            raise ContractError(f"Path cannot contain '..': {p}")
        if "\\" in p:
            raise ContractError(f"Path cannot contain \\: {p}")
        if p in seen:
            raise ContractError(f"Duplicate path: {p}")
        seen.add(p)
