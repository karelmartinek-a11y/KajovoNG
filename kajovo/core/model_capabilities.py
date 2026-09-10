from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from .openai_client import OpenAIClient
from .request_rules import is_non_response_model, uses_reasoning_defaults
from .structured_output import prepare_payload, validate_output
from .utils import atomic_write_text
from .retry import with_retry, CircuitBreaker


def split_text(text: str, max_chars: int) -> List[str]:
    if not text:
        return [""]
    if max_chars <= 0:
        return [text]
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        out.append(text[i : i + max_chars])
        i += max_chars
    return out


def _mk_parts(text: str, max_chars: int) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    chunks = split_text(text, max_chars=max_chars)
    if not chunks:
        chunks = [""]
    for ch in chunks:
        parts.append(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": ch}],
            }
        )
    return parts


def _err_indicates_param_unsupported(err: str, param_name: str) -> bool:
    """Heuristicky rozpozná odmítnutí schématu nebo parametru.

    Přechodné chyby 429, 5xx a sítě samy neznamenají nepodporovanou schopnost."""
    if not err:
        return False
    e = err.lower()
    key = param_name.lower()
    # Rozpoznávané formulace chyb SDK a proxy.
    needles = [
        f"unknown parameter: {key}",
        f"unrecognized parameter: {key}",
        f"unexpected parameter: {key}",
        f"unsupported parameter: {key}",
        "additional properties are not allowed",
        "extra fields not permitted",
        f"'{key}' is not permitted",
        f"'{key}' was unexpected",
        f"{key} is not allowed",
        f"{key} is not supported",
    ]
    if any(n in e for n in needles) and (key in e):
        return True
    # Rozpoznání neznámého parametru ve validační zprávě.
    if (
        key in e
        and ("unknown" in e or "unrecognized" in e or "unsupported" in e)
        and ("parameter" in e or "field" in e)
    ):
        return True
    return False


def _try_response(
    client: OpenAIClient,
    settings,
    breaker: CircuitBreaker,
    payload: Dict[str, Any],
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    try:
        prepare_payload(payload)
        if isinstance(client, OpenAIClient):
            client._policy.check_documented(payload)
            resp = client._policy.probe(payload)
        else:
            controller = getattr(client, "cost_control", None)
            resp = controller.execute(client, payload, stage="PROBE") if controller else client.create_response(payload)
        validate_output(resp, payload)
        return True, resp, None
    except Exception as e:
        return False, None, str(e)


@dataclass
class ModelCapabilities:
    model: str
    tested_at: float
    ok_basic: bool

    supports_previous_response_id: bool
    supports_temperature: bool
    supports_tools: bool
    supports_file_search: bool
    supports_vector_store: bool = False
    supports_structured_outputs: bool = False

    notes: str = ""
    errors: Dict[str, str] = None  # type: ignore

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["errors"] = self.errors or {}
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ModelCapabilities":
        return ModelCapabilities(
            model=str(d.get("model", "")),
            tested_at=float(d.get("tested_at", 0.0)),
            ok_basic=bool(d.get("ok_basic", False)),
            supports_previous_response_id=d.get("supports_previous_response_id") is True,
            supports_temperature=d.get("supports_temperature") is True,
            supports_structured_outputs=d.get("supports_structured_outputs") is True,
            supports_tools=bool(d.get("supports_tools", False)),
            supports_file_search=bool(d.get("supports_file_search", False)),
            supports_vector_store=bool(
                d.get("supports_vector_store", d.get("supports_file_search", False))
            ),
            notes=str(d.get("notes", "")),
            errors=dict(d.get("errors") or {}),
        )


class ModelCapabilitiesCache:
    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, ModelCapabilities] = {}
        self.identity = None
        self._force_refresh_marker = f"{self.path}.force_refresh"

    @staticmethod
    def _apply_error_overrides(caps: ModelCapabilities) -> ModelCapabilities:
        """Sjednotí příznaky schopností podle uložených chyb."""
        errs = caps.errors or {}
        # Podpora teploty se odvozuje z uložených chyb parametru.
        for msg in errs.values():
            if not msg:
                continue
            if _err_indicates_param_unsupported(str(msg), "temperature"):
                caps.supports_temperature = False
                break
            if "unsupported parameter" in str(msg).lower() and "temperature" in str(msg).lower():
                caps.supports_temperature = False
                break
        # Výslovné odmítnutí previous_response_id.
        if "previous_response_id_param" in errs:
            caps.supports_previous_response_id = False
        return caps

    def bind(self, api_key, base_url="https://api.openai.com/v1"):
        self.identity = hashlib.sha256(base_url.rstrip("/").encode()).hexdigest()
        self.load()

    def load(self) -> None:
        self._data = {}
        if os.path.isfile(self._force_refresh_marker):
            self._clear_force_refresh()
            return
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                root = json.load(f)
            legacy = root.get("version") == 3 and self.identity == hashlib.sha256(b"https://api.openai.com/v1").hexdigest()
            if self.identity is not None and not legacy and (root.get("identity") != self.identity or root.get("version") != 4):
                return
            models = (root or {}).get("models") or {}
            for mid, obj in models.items():
                if isinstance(obj, dict):
                    caps = ModelCapabilities.from_dict(obj)
                    if legacy and not (caps.ok_basic and caps.supports_structured_outputs):
                        continue
                    self._data[mid] = self._apply_error_overrides(caps)
        except Exception:
            self._data = {}

    def _clear_force_refresh(self) -> None:
        """Odstraní příznak vynuceného obnovení a uloženou cache."""
        try:
            os.remove(self._force_refresh_marker)
        except Exception:
            pass
        try:
            os.remove(self.path)
        except Exception:
            pass
        self._data = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        root = {
            "version": 4,
            "identity": self.identity,
            "saved_at": time.time(),
            "models": {k: v.to_dict() for k, v in self._data.items()},
        }
        atomic_write_text(self.path, json.dumps(root, ensure_ascii=False, indent=2))

    def get(self, model: str) -> Optional[ModelCapabilities]:
        return self._data.get(model)

    def upsert(self, caps: ModelCapabilities) -> None:
        self._data[caps.model] = caps

    def is_stale(self, model: str, ttl_hours: float) -> bool:
        c = self.get(model)
        if not c:
            return True
        if ttl_hours <= 0:
            return False
        age = time.time() - float(c.tested_at or 0)
        return age > ttl_hours * 3600.0

    def missing_or_stale(self, models: List[str], ttl_hours: float) -> List[str]:
        out: List[str] = []
        for m in models:
            if self.is_stale(m, ttl_hours):
                out.append(m)
        return out


class ModelProbeWorker(QThread):
    progress = Signal(int)  # 0..100
    model_status = Signal(str, str)  # Identifikátor modelu a stavová zpráva.
    logline = Signal(str)

    def __init__(
        self,
        settings,
        api_key: str,
        cache: ModelCapabilitiesCache,
        models_to_probe: List[str],
        ttl_hours: float = 24.0,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.api_key = api_key
        self.cache = cache
        self.models_to_probe = [model for model in models_to_probe if not is_non_response_model(model)]
        self.ttl_hours = ttl_hours
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        if not self.api_key:
            self.logline.emit("Model probe: missing OPENAI_API_KEY.")
            return

        client = OpenAIClient(self.api_key, timeout_s=self.settings.response_timeout_s)
        client.configure_validation(self.settings, getattr(self, "cost_control", None))
        self.cache.bind(self.api_key)
        available = {row["id"] for row in client.list_models()}
        self.models_to_probe = [model for model in self.models_to_probe if model in available]
        breaker = CircuitBreaker(
            self.settings.retry.circuit_breaker_failures,
            self.settings.retry.circuit_breaker_cooldown_s,
        )

        # Sdílené úložiště a soubor pro ověření file_search.
        vs_id: Optional[str] = None
        fs_ready = False
        fid = None
        tmp_path = None
        try:
            vs = with_retry(
                lambda: client.create_vector_store(
                    f"caps_probe_{int(time.time())}", expires_after_days=1
                ),
                self.settings.retry,
                breaker,
            )
            vs_id = vs.get("id")
            if vs_id:
                tmp_dir = getattr(self.settings, "cache_dir", "cache")
                os.makedirs(tmp_dir, exist_ok=True)
                tmp_path = os.path.join(tmp_dir, f"_caps_probe_file_{int(time.time())}.txt")
                needle = f"NEEDLE_{int(time.time())}"
                with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(f"hello\n{needle}\nbye\n")
                up = with_retry(
                    lambda: client.upload_file(tmp_path, purpose="user_data"),
                    self.settings.retry,
                    breaker,
                )
                fid = up.get("id")
                if fid:
                    with_retry(
                        lambda: client.add_file_to_vector_store(
                            vs_id, fid, attributes={"source_path": tmp_path}
                        ),
                        self.settings.retry,
                        breaker,
                    )
                    deadline = time.monotonic() + 120
                    while not self._stop and time.monotonic() < deadline:
                        status = client.retrieve_vector_store_file(vs_id, fid).get("status")
                        if status == "completed":
                            fs_ready = True
                            break
                        if status in ("failed", "cancelled"):
                            break
                        time.sleep(1)
        except Exception as e:
            self.logline.emit(f"Model probe: file_search setup failed (best-effort): {e}")
            fs_ready = False

        try:
            total = max(1, len(self.models_to_probe))
            for idx, model_id in enumerate(self.models_to_probe):
                if self._stop:
                    self.logline.emit("Model probe: stopped.")
                    break

                if self.ttl_hours > 0 and not self.cache.is_stale(model_id, self.ttl_hours):
                    self.model_status.emit(model_id, "cached (skip)")
                    self.progress.emit(int(((idx + 1) * 100) / total))
                    continue

                self.model_status.emit(model_id, "probing...")
                self.progress.emit(int((idx * 100) / total))

                caps = self._probe_one(client, breaker, model_id, vs_id=vs_id if fs_ready else None)
                if getattr(getattr(client, "cost_control", None), "cancelled", False):
                    break
                self.cache.upsert(caps)
                try:
                    self.cache.save()
                except Exception:
                    pass

                self.model_status.emit(model_id, "ok" if caps.ok_basic else "failed")
                self.logline.emit(
                    f"CAPS {model_id}: basic={caps.ok_basic} prev_id={caps.supports_previous_response_id} "
                    f"temp={caps.supports_temperature} tools={caps.supports_tools} file_search={caps.supports_file_search}"
                )
                self.progress.emit(int(((idx + 1) * 100) / total))
        finally:
            for identifier, delete in (
                (vs_id, client.delete_vector_store),
                (fid, client.delete_file),
            ):
                if identifier:
                    try:
                        delete(identifier)
                    except Exception as exc:
                        self.logline.emit(f"Úklid prostředků Probe selhal ({identifier}): {exc}")
            if tmp_path and os.path.isfile(tmp_path):
                os.remove(tmp_path)

    def _probe_one(
        self,
        client: OpenAIClient,
        breaker: CircuitBreaker,
        model_id: str,
        vs_id: Optional[str],
    ) -> ModelCapabilities:
        errs: Dict[str, str] = {}

        # Základní požadavek.
        payload_basic: Dict[str, Any] = {
            "model": model_id,
            "instructions": 'Return ONLY valid JSON: {"contract":"CAP_PING","ok":true}. No extra text.',
            "input": _mk_parts("ping", max_chars=20000),
        }
        ok_basic, resp1, err1 = _try_response(client, self.settings, breaker, payload_basic)
        if not ok_basic or not resp1:
            errs["basic"] = err1 or "unknown"
            return ModelCapabilities(
                model=model_id,
                tested_at=time.time(),
                ok_basic=False,
                supports_previous_response_id=False,
                supports_temperature=False,
                supports_tools=False,
                supports_file_search=False,
                supports_vector_store=False,
                notes="basic call failed",
                errors=errs,
            )

        resp1_id = str(resp1.get("id", ""))

        # Samotné získání ID neprokazuje podporu návaznosti.
        supports_prev = False

        # Ověření návaznosti; rozhoduje výslovné odmítnutí parametru.
        if resp1_id:
            payload_prev: Dict[str, Any] = {
                "model": model_id,
                "instructions": 'Return ONLY valid JSON: {"contract":"CAP_PREV","ok":true}. No extra text.',
                "input": _mk_parts("pong", max_chars=20000),
                "previous_response_id": resp1_id,
            }
            ok_prev, _, errp = _try_response(client, self.settings, breaker, payload_prev)
            if ok_prev:
                supports_prev = True
            else:
                if errp:
                    # Podpora se odmítá pouze při chybě schématu nebo parametru.
                    if _err_indicates_param_unsupported(errp, "previous_response_id"):
                        supports_prev = False
                        errs["previous_response_id_param"] = errp
                    else:
                        supports_prev = False
                        errs["previous_response_id_inconclusive"] = errp

        # Ověření jiné než výchozí teploty.
        supports_temp = not uses_reasoning_defaults(model_id)
        payload_temp: Dict[str, Any] = {
            "model": model_id,
            "temperature": 1.1,
            "instructions": 'Return ONLY valid JSON: {"contract":"CAP_TEMP","ok":true}. No extra text.',
            "input": _mk_parts("temp", max_chars=20000),
        }
        if supports_temp:
            ok_temp, _, errt = _try_response(client, self.settings, breaker, payload_temp)
        else:
            ok_temp, errt = False, None
        if ok_temp:
            supports_temp = True
        elif uses_reasoning_defaults(model_id):
            supports_temp = False
        else:
            if errt and _err_indicates_param_unsupported(errt, "temperature"):
                supports_temp = False
                errs["temperature_param"] = errt
            else:
                supports_temp = False
                if errt:
                    errs["temperature_inconclusive"] = errt

        # Ověření file_search se sdíleným úložištěm.
        supports_tools = False
        supports_file_search = False
        if vs_id:
            payload_tools: Dict[str, Any] = {
                "model": model_id,
                "instructions": (
                    "Try to use file_search tool. Return ONLY valid JSON: "
                    '{"contract":"CAP_TOOLS","ok":true}. No extra text.'
                ),
                "input": _mk_parts(
                    "Search in files for the word NEEDLE and confirm you used file_search.",
                    max_chars=20000,
                ),
                "tools": [{"type": "file_search", "vector_store_ids": [vs_id]}],
                "tool_choice": {"type": "file_search"},
            }
            ok_tools, tool_response, errx = _try_response(client, self.settings, breaker, payload_tools)
            if ok_tools and any(item.get("type") == "file_search_call" for item in (tool_response or {}).get("output", [])):
                supports_tools = True
                supports_file_search = True
            else:
                if errx and _err_indicates_param_unsupported(errx, "tools"):
                    supports_tools = False
                    supports_file_search = False
                    errs["tools_param"] = errx
                else:
                    # Neprůkazný výsledek ponechá funkci vypnutou a zaznamená důvod.
                    errs["tools_inconclusive"] = errx or "unknown"

        return ModelCapabilities(
            model=model_id,
            tested_at=time.time(),
            ok_basic=True,
            supports_structured_outputs=True,
            supports_previous_response_id=supports_prev,
            supports_temperature=supports_temp,
            supports_tools=supports_tools,
            supports_file_search=supports_file_search,
            supports_vector_store=supports_file_search,
            notes="ok",
            errors=errs,
        )
