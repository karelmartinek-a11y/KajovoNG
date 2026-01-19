# api_logic.py
from __future__ import annotations

import os
import json
import time
import hashlib
import logging
import atexit
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional

from openai import OpenAI
import httpx

log = logging.getLogger(__name__)

_client: Optional[OpenAI] = None
_api_key: Optional[str] = None
_http_client: Optional[httpx.Client] = None


def _make_http_client() -> httpx.Client:
    # Vlastní httpx klient: obejde interní wrapper v openai-python,
    # který v některých kombinacích verzí posílá do httpx.Client() neplatný parametr 'proxies'.
    timeout = httpx.Timeout(60.0, connect=15.0)
    return httpx.Client(timeout=timeout, follow_redirects=True)


def _close_http_client() -> None:
    global _http_client
    try:
        if _http_client is not None:
            _http_client.close()
    finally:
        _http_client = None


atexit.register(_close_http_client)

def set_api_key(key: str) -> None:
    """
    Nastaví API klíč pro OpenAI klienta (globálně v rámci aplikace).

    Pozn.: Některé kombinace verzí openai-python + httpx (zejména httpx 0.28+)
    končí chybou `Client.__init__() got an unexpected keyword argument 'proxies'`.
    Proto vytváříme vlastní `httpx.Client` a předáme ho do OpenAI klienta přes `http_client=...`,
    čímž obejdeme interní wrapper.
    """
    global _client, _api_key, _http_client
    _api_key = key

    # zavři starý klient (pokud byl)
    _close_http_client()
    _http_client = _make_http_client()

    try:
        _client = OpenAI(api_key=key, http_client=_http_client)
    except TypeError:
        # fallback pro velmi staré verze openai-python, které `http_client` nemají.
        # V takovém případě je potřeba fixnout závislosti (httpx < 0.28).
        _client = OpenAI(api_key=key)

    log.info("Nastavuji API klíč (client init).")


def _client_required() -> OpenAI:
    if _client is None:
        # OpenAI knihovna umožní i env var, ale pro Káju chceme konzistentně registry/API-KEY tlačítko.
        raise RuntimeError("API klíč není nastaven. Použij tlačítko API-KEY.")
    return _client


def list_available_models() -> List[str]:
    """
    Vrátí dostupné modely pro účet – vždy reálně z API (bez statických seznamů).
    """
    c = _client_required()
    try:
        result = c.models.list()
    except Exception as exc:
        log.exception("Nepodařilo se načíst modely: %s", exc)
        return []

    models: List[str] = []
    for m in result:
        mid = getattr(m, "id", "")
        if mid.startswith("gpt") and "embed" not in mid and "moderation" not in mid:
            models.append(mid)
    models.sort()
    return models


# ============================================================
# PRICING / COST
# ============================================================

def _extract_usage_tokens(resp: Any) -> Tuple[int, int]:
    """
    Z Responses objektu vytáhne input/output tokeny.
    """
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")

    inp = 0
    out = 0

    if usage:
        inp = int(getattr(usage, "input_tokens", 0) or (usage.get("input_tokens") if isinstance(usage, dict) else 0))
        out = int(getattr(usage, "output_tokens", 0) or (usage.get("output_tokens") if isinstance(usage, dict) else 0))
    return inp, out


def combine_costs(usages: List[Tuple[int, int]], model: str, pricing_table: Dict[str, Dict[str, float]] | None = None) -> Tuple[float, int, int]:
    """
    Sečte tokeny a cenu.
    """
    if pricing_table is None:
        # fallback (musí sedět s tvým pricing.py; tady je jen fail-safe)
        pricing_table = {
            "gpt-5.1": {"input": 0.000002, "output": 0.000006},
            "gpt-5.0": {"input": 0.000002, "output": 0.000006},
            "gpt-4.1": {"input": 0.0000005, "output": 0.0000015},
            "gpt-4.1-mini": {"input": 0.0000003, "output": 0.0000009},
            "gpt-4o": {"input": 0.0000005, "output": 0.0000015},
        }

    tin = sum(u[0] for u in usages)
    tout = sum(u[1] for u in usages)

    p = pricing_table.get(model)
    if not p:
        return 0.0, tin, tout

    cost = tin * p["input"] + tout * p["output"]
    # zaokrouhlení kvůli UI
    cost = float(f"{cost:.6f}")
    return cost, tin, tout


# ============================================================
# FILE API – PER-FILE UPLOAD + MANIFEST
# ============================================================

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".idea", ".vscode", ".kaja"
}

SKIP_EXTS = {
    ".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".zip", ".7z", ".rar", ".iso"
}

TEXT_SAFE_EXTS = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".ini", ".cfg", ".toml",
    ".css", ".qss", ".ui", ".xml", ".html", ".js", ".ts"
}

MANIFEST_DIRNAME = ".kaja"
MANIFEST_FILENAME = "manifest.json"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_project_files(root_dir: str) -> List[str]:
    """
    Vrátí seznam cest k souborům v projektu (absolutní).
    """
    out: List[str] = []
    for base, dirs, files in os.walk(root_dir):
        # skip dirs in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXTS:
                continue
            full = os.path.join(base, fn)
            out.append(full)

    out.sort()
    return out


def _rel_path(root_dir: str, full_path: str) -> str:
    rp = os.path.relpath(full_path, root_dir)
    return rp.replace("\\", "/")


def _manifest_path(root_dir: str) -> str:
    d = os.path.join(root_dir, MANIFEST_DIRNAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, MANIFEST_FILENAME)


def load_manifest(root_dir: str) -> Dict[str, Any] | None:
    mp = _manifest_path(root_dir)
    if not os.path.exists(mp):
        return None
    with open(mp, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(root_dir: str, manifest: Dict[str, Any]) -> None:
    mp = _manifest_path(root_dir)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def upload_file_to_openai(path: str, purpose: str = "assistants") -> str:
    """
    Nahraje 1 soubor na OpenAI Files API a vrátí file_id.
    """
    c = _client_required()
    filename = os.path.basename(path)
    log.info("UPLOAD FILE: %s", filename)
    with open(path, "rb") as f:
        obj = c.files.create(file=f, purpose=purpose)
    file_id = getattr(obj, "id", None) or (obj.get("id") if isinstance(obj, dict) else None)
    if not file_id:
        raise RuntimeError("Upload souboru nevrátil file_id.")
    return str(file_id)


def upload_project_per_file(root_dir: str, progress_cb=None) -> Tuple[Dict[str, Any], List[str]]:
    """
    Nahraje projekt jako jednotlivé soubory.
    Vytvoří manifest:
      manifest["files"][rel_path] = {file_id, sha256, size, mtime}
    """
    files = _iter_project_files(root_dir)
    if not files:
        raise RuntimeError("V projektu nebyly nalezeny žádné soubory pro upload.")

    manifest: Dict[str, Any] = {
        "root_dir": root_dir,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": {}
    }

    file_ids: List[str] = []

    total = len(files)
    for idx, full in enumerate(files, start=1):
        rp = _rel_path(root_dir, full)
        ext = os.path.splitext(rp)[1].lower()

        # pokud je to jasně binární – přeskoč
        # (per-file režim je primárně na textové soubory pro LLM iterace)
        if ext and ext not in TEXT_SAFE_EXTS and ext in {".png", ".jpg", ".jpeg", ".mp4", ".mov"}:
            # tyto typy typicky nechceš do revize kódu
            continue

        if progress_cb:
            progress_cb(f"UPLOAD {idx}/{total}: {rp}")

        fid = upload_file_to_openai(full, purpose="assistants")
        sha = _sha256_file(full)
        st = os.stat(full)

        manifest["files"][rp] = {
            "file_id": fid,
            "sha256": sha,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        }
        file_ids.append(fid)

    save_manifest(root_dir, manifest)
    return manifest, file_ids


def build_manifest_text(manifest: Dict[str, Any]) -> str:
    """
    Vytvoří textový blok do promptu: cesta -> file_id.
    """
    lines = ["MANIFEST (path -> file_id):"]
    files = manifest.get("files", {})
    for path in sorted(files.keys()):
        fid = files[path].get("file_id", "")
        lines.append(f"- {path} -> {fid}")
    return "\n".join(lines)


# ============================================================
# RESPONSES – REVIZE / MODIFY nad file_ids
# ============================================================

def _extract_output_text(resp: Any) -> str:
    """
    Extrahuje text z Responses objektu (nejrobustnější varianta).
    """
    # openai python SDK: resp.output -> list blocks
    output = getattr(resp, "output", None)
    if output is None and isinstance(resp, dict):
        output = resp.get("output", [])

    if not output:
        return ""

    try:
        first = output[0]
        content = getattr(first, "content", None) or (first.get("content") if isinstance(first, dict) else [])
        if not content:
            return ""
        c0 = content[0]
        # text může být dict/value
        txt = getattr(c0, "text", None) or (c0.get("text") if isinstance(c0, dict) else None)
        if isinstance(txt, dict):
            return str(txt.get("value", ""))
        if txt is None:
            return ""
        return str(txt)
    except Exception:
        return ""


def ask_or_modify_with_file_ids(
    model: str,
    file_ids: List[str],
    manifest: Dict[str, Any],
    user_prompt: str,
    mode: str,
    progress_cb=None,
    timeout: int = 120,
) -> Tuple[str, Tuple[int, int]]:
    """
    mode:
      - "answer" => vrátí čistý text
      - "modify" => vrátí JSON {mode:'patches', files:[{path,content},...], note:''}
    """
    if mode not in ("answer", "modify"):
        raise ValueError("mode musí být 'answer' nebo 'modify'.")

    c = _client_required()

    manifest_text = build_manifest_text(manifest)

    if mode == "answer":
        dev = (
            "Jsi code reviewer. Odpověz pouze textem (žádný JSON), stručně a přesně.\n"
            "Pracuj nad připojenými soubory z File API. Manifest mapuje cesty na file_id."
        )
    else:
        dev = (
            "Jsi senior software engineer. Vrátíš POUZE validní JSON (bez markdown), formát:\n"
            "{"
            "\"mode\":\"patches\","
            "\"files\":[{\"path\":\"...\",\"content\":\"...\"},...],"
            "\"note\":\"...\""
            "}\n"
            "V JSON nesmí být nic navíc. Path musí odpovídat manifestu."
        )

    if progress_cb:
        progress_cb("Odesílám požadavek do Responses API...")

    resp = c.responses.create(
        model=model,
        input=[
            {"role": "developer", "content": [{"type": "input_text", "text": dev}]},
            {"role": "developer", "content": [{"type": "input_text", "text": manifest_text}]},
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}] + [{"type": "input_file", "file_id": fid} for fid in file_ids],
            },
        ],
        tools=[
            {
                "type": "code_interpreter",
                "container": {"type": "auto", "file_ids": file_ids},
            }
        ],
        timeout=timeout,
    )

    text = _extract_output_text(resp)
    usage = _extract_usage_tokens(resp)
    return text, usage


def apply_patches_and_reupload(
    root_dir: str,
    manifest: Dict[str, Any],
    patches_json_text: str,
    progress_cb=None,
) -> Tuple[List[str], Dict[str, Any], List[str]]:
    """
    Vezme JSON z MODIFY, přepíše soubory na disk, znovu je nahraje na File API,
    aktualizuje manifest a vrátí seznam změněných path + nový manifest + nové file_ids (jen ty změněné).
    """
    try:
        data = json.loads(patches_json_text)
    except Exception as exc:
        raise RuntimeError(f"Model nevrátil validní JSON: {exc}")

    if data.get("mode") != "patches":
        raise RuntimeError("JSON nemá mode='patches'.")

    files = data.get("files", [])
    if not isinstance(files, list):
        raise RuntimeError("JSON 'files' není list.")

    changed_paths: List[str] = []
    new_file_ids: List[str] = []

    for item in files:
        path = item.get("path")
        content = item.get("content", "")
        if not path:
            continue

        full = os.path.join(root_dir, path.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

        changed_paths.append(path)

        if progress_cb:
            progress_cb(f"REUPLOAD změněného souboru: {path}")

        fid = upload_file_to_openai(full, purpose="assistants")
        sha = _sha256_file(full)
        st = os.stat(full)

        manifest.setdefault("files", {})
        manifest["files"][path] = {
            "file_id": fid,
            "sha256": sha,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        }
        new_file_ids.append(fid)

    save_manifest(root_dir, manifest)
    return changed_paths, manifest, new_file_ids
