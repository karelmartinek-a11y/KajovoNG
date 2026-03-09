from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import collections


try:
    from PySide6.QtCore import QObject, Signal, QThread
except ModuleNotFoundError:
    # Dummy fallback classes for non-GUI environments (e.g., CI)
    class _DummySignal:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass

    class QObject:
        pass

    # We emulate Signal by returning _DummySignal
    def Signal(*args, **kwargs):
        return _DummySignal()

    class QThread:
        pass

from .contracts import ContractError, extract_text_from_response, parse_json_strict, validate_paths
from .filescan import build_manifest, scan_tree
from .openai_client import OpenAIClient
from .pricing import PriceTable, compute_cost
from .pricing_fetcher import PricingFetcher
from .receipt import Receipt, ReceiptDB
from .retry import CircuitBreaker, with_retry
from .utils import ensure_dir, is_versing_snapshot_dir, sha256_file, ts_code
from .model_capabilities import ModelCapabilities

SUPPORTED_INPUT_FILE_EXTS = {
    ".art", ".bat", ".brf", ".c", ".cls", ".css", ".csv", ".diff", ".doc", ".docx", ".dot", ".eml", ".es",
    ".h", ".hs", ".htm", ".html", ".hwp", ".hwpx", ".ics", ".ifb", ".java", ".js", ".json", ".keynote",
    ".ksh", ".ltx", ".mail", ".markdown", ".md", ".mht", ".mhtml", ".mjs", ".nws", ".odt", ".pages", ".patch",
    ".pdf", ".pl", ".pm", ".pot", ".ppa", ".pps", ".ppt", ".pptx", ".pwz", ".py", ".rst", ".rtf", ".scala",
    ".sh", ".shtml", ".srt", ".sty", ".tex", ".text", ".txt", ".vcf", ".vtt", ".wiz",
    ".xla", ".xlb", ".xlc", ".xlm", ".xls", ".xlsx", ".xlt", ".xlw", ".xml", ".yaml", ".yml",
}
SUPPORTED_INPUT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


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


@dataclass
class UiRunConfig:
    project: str
    prompt: str
    mode: str  # GENERATE|MODIFY|QA|QFILE
    send_as_c: bool
    model_default: str
    model_a1: Optional[str]
    model_a2: Optional[str]
    model_a3: Optional[str]
    response_id: str
    attached_file_ids: List[str]
    input_file_ids: List[str]
    attached_vector_store_ids: List[str]
    in_dir: str
    out_dir: str
    in_equals_out: bool
    versing: bool
    temperature: float
    use_file_search: bool

    diag_windows_in: bool
    diag_windows_out: bool
    diag_ssh_in: bool
    diag_ssh_out: bool
    ssh_user: str
    ssh_host: str
    ssh_key: str
    ssh_password: str
    skip_paths: List[str]
    skip_exts: List[str]

    # capabilities snapshots (cached probe)
    model_caps_default: ModelCapabilities
    model_caps_a1: Optional[ModelCapabilities] = None
    model_caps_a2: Optional[ModelCapabilities] = None
    model_caps_a3: Optional[ModelCapabilities] = None
    # resume data for rerun (precomputed structure + prev_id)
    resume_files: List[Dict[str, Any]] = None  # type: ignore
    resume_prev_id: Optional[str] = None

    @staticmethod
    def _stage_key(stage: str) -> str:
        key = str(stage or "").strip().upper()
        if ":" in key:
            key = key.split(":", 1)[0]
        if "_" in key:
            key = key.split("_", 1)[0]
        return key

    def model_for_stage(self, stage: str) -> str:
        key = self._stage_key(stage)
        if key.startswith("A1") and self.model_a1:
            return self.model_a1
        if key.startswith("A2") and self.model_a2:
            return self.model_a2
        if key.startswith("A3") and self.model_a3:
            return self.model_a3
        return self.model_default

    def caps_for_stage(self, stage: str) -> ModelCapabilities:
        key = self._stage_key(stage)
        if key.startswith("A1") and self.model_caps_a1:
            return self.model_caps_a1
        if key.startswith("A2") and self.model_caps_a2:
            return self.model_caps_a2
        if key.startswith("A3") and self.model_caps_a3:
            return self.model_caps_a3
        return self.model_caps_default

    # Backward-compatible aliases for legacy code paths.
    @property
    def model(self) -> str:
        return self.model_default

    @property
    def model_caps(self) -> Dict[str, Any]:
        caps = self.model_caps_default
        return caps.to_dict() if caps else {}


class RunWorker(QThread):
    progress = Signal(int)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)

    def __init__(
        self,
        cfg: UiRunConfig,
        settings,
        api_key: str,
        run_logger,
        receipt_db: ReceiptDB,
        price_table: PriceTable,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.cfg = cfg
        self.settings = settings
        self.api_key = api_key
        self.log = run_logger
        self.db = receipt_db
        self.price_table = price_table
        self.breaker = CircuitBreaker(settings.retry.circuit_breaker_failures, settings.retry.circuit_breaker_cooldown_s)
        self._stop = False
        self._has_receipt = False
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._used_file_search = False
        self._last_prev_id_error: Optional[str] = None
        self._final_response_id: Optional[str] = None
        self._in_dir_info: Optional[Dict[str, Any]] = None
        self._fs_tools: Optional[List[Dict[str, Any]]] = None
        self._vector_store_ids: List[str] = []
        self._diag_vector_store_ids: List[str] = []
        self._diag_text: str = ""
        self._diag_zip_path: str = ""
        self._input_kind_cache: Dict[str, str] = {}
        self._file_name_cache: Dict[str, str] = {}

    def _ts(self) -> str:
        return time.strftime("%Y%m%d %H%M%S")

    def _log_debug(self, msg: str) -> None:
        line = f"{self._ts()} | {msg}"
        try:
            self.logline.emit(line)
            self.log.event("debug", {"ts": self._ts(), "msg": msg})
        except Exception:
            pass

    def _log_api_action(self, stage: str, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        ts = self._ts()
        model = self._generate_model(stage)
        merged: Dict[str, Any] = {}
        if details:
            merged.update(details)
        if model and merged.get("model") is None:
            merged["model"] = model
        parts = [f"{stage}: {action}"]
        if merged:
            for key, value in merged.items():
                if value is None:
                    continue
                parts.append(f"{key}={value}")
        line = f"{ts} | " + " | ".join(parts)
        try:
            self.logline.emit(line)
            event = {"ts": ts, "stage": stage, "action": action, "model": model}
            if merged:
                event.update({k: v for k, v in merged.items() if v is not None})
            self.log.event("api.trace", event)
            if event.get("response_id"):
                try:
                    self._final_response_id = str(event.get("response_id") or "")
                except Exception:
                    pass
                patch = {"last_response_id": str(event.get("response_id")), "last_response_stage": stage}
                contract = event.get("contract") or merged.get("contract") if merged else None
                if contract in ("A2_STRUCTURE", "B2_STRUCTURE"):
                    patch["last_structure_response_id"] = str(event.get("response_id"))
                if contract in ("A1_PLAN", "A2_STRUCTURE", "B1_PLAN", "B2_STRUCTURE"):
                    patch["last_plan_response_id"] = str(event.get("response_id"))
                try:
                    self.log.update_state(patch)
                except Exception:
                    pass
        except Exception:
            pass

    def _attachments_snapshot(
        self,
        stage: str,
        ref_file_ids: List[str],
        input_file_ids: List[str],
        input_image_ids: List[str],
        vector_store_ids: List[str],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        ts = self._ts()
        caps = self._caps_for_step(stage)
        if not isinstance(caps, dict):
            if hasattr(caps, "to_dict"):
                caps = caps.to_dict()
            else:
                caps = {}
        ref_ids = [fid for fid in (ref_file_ids or []) if fid]
        input_ids = [fid for fid in (input_file_ids or []) if fid]
        image_ids = [fid for fid in (input_image_ids or []) if fid]
        vs_ids = [vid for vid in (vector_store_ids or []) if vid]
        tool_types = [t.get("type") for t in (tools or []) if isinstance(t, dict)]
        return {
            "ts": ts,
            "stage": stage,
            "file_ids": ref_ids,
            "input_file_ids": input_ids,
            "input_image_ids": image_ids,
            "vector_store_ids": vs_ids,
            "tool_types": tool_types,
            "use_file_search": bool(self.cfg.use_file_search),
            "supports_file_search": bool(caps.get("supports_file_search", False)),
            "supports_vector_store": bool(caps.get("supports_vector_store", False)),
        }

    def _input_file_ids(self) -> List[str]:
        try:
            if hasattr(self.cfg, "input_file_ids"):
                return list(self.cfg.input_file_ids or [])
        except Exception:
            pass
        return list(self.cfg.attached_file_ids or [])

    def _generate_model(self, step: str) -> str:
        chosen = ""
        try:
            chosen = self.cfg.model_for_stage(step)
        except Exception:
            chosen = getattr(self.cfg, "model_default", "") or ""
        default_model = str(getattr(self.cfg, "model_default", "") or "").strip()
        return (str(chosen or default_model) or "").strip()

    def _caps_for_step(self, step: str) -> Dict[str, Any]:
        model = self._generate_model(step)
        cache = getattr(self.settings, "model_cap_cache", None)
        if cache:
            try:
                caps = cache.get(model)
                if caps:
                    if hasattr(caps, "to_dict"):
                        return caps.to_dict()
                    if isinstance(caps, dict):
                        return caps
            except Exception:
                pass
        try:
            caps_obj = self.cfg.caps_for_stage(step)
            if caps_obj and hasattr(caps_obj, "to_dict"):
                return caps_obj.to_dict()
        except Exception:
            pass
        try:
            caps_dict = getattr(self.cfg, "model_caps", None)
            if isinstance(caps_dict, dict):
                return caps_dict
        except Exception:
            pass
        return {}

    def _dedupe_files(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        seen = set()
        for f in files:
            path = f.get("path")
            if not isinstance(path, str) or not path:
                continue
            if path in seen:
                continue
            seen.add(path)
            out.append(f)
        return out

    def _request_model_fields(self) -> Dict[str, Any]:
        return {
            "model_default": self.cfg.model_default,
            "model_a1": self.cfg.model_a1,
            "model_a2": self.cfg.model_a2,
            "model_a3": self.cfg.model_a3,
        }

    def _ui_state_for_log(self) -> Dict[str, Any]:
        state = dict(self.cfg.__dict__)
        for key in ("model_caps_default", "model_caps_a1", "model_caps_a2", "model_caps_a3"):
            caps = state.get(key)
            if isinstance(caps, ModelCapabilities):
                state[key] = caps.to_dict()
        # backward-compatible aliases for older loaders
        state["model"] = self.cfg.model_default
        state["model_caps"] = self.cfg.model_caps_default.to_dict()
        return state

    # ---------- artifact helpers ----------
    def _save_json_artifact(self, name: str, payload: Any) -> str:
        rel = str(name or "").strip().replace("\\", "/")
        if not rel:
            raise ValueError("artifact name cannot be empty")
        if rel.startswith("/") or ".." in rel.split("/"):
            raise ValueError("artifact name must be relative")
        path = os.path.join(self.log.paths.responses_dir, f"{rel}.json")
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        try:
            self.log.event("file.saved.responses", {"path": path, "bytes": os.path.getsize(path)})
        except Exception:
            pass
        return path

    def _load_json_artifact(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _generate_modules_plan(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a list of project‑level modules with a heuristically inferred purpose.

        The heuristic is deterministic and **does not** inspect file contents –
        only file paths and any provided *purpose* metadata. For every top‑level
        directory *X/* we count how many files map to the high‑level roles
        (service, controller, repository, model, schema, etc.) and describe the
        module by its dominant role, producing compact human‑readable text
        useful for A3 reasoning.

        Example::
            {'modules': [{'name': 'services', 'purpose': 'Service‑oriented code'},
                         {'name': 'models',   'purpose': 'Domain models'}]}
        """
        role_labels = {
            "service": "Service‑oriented code",
            "controller": "Request/response orchestration",
            "repository": "Persistence layer",
            "model": "Domain models",
            "schema": "Serialization / validation schemas",
            "handler": "Event / job handlers",
            "utility": "Shared utilities",
            "config": "Configuration helpers",
        }
        module_roles: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for f in files:
            path = f.get("path") or ""
            purpose = str(f.get("purpose") or "")
            if not isinstance(path, str) or not path:
                continue
            parts = path.split("/")
            if not parts:
                continue
            mod = parts[0]
            role = self._infer_file_role(path, purpose)
            module_roles[mod][role] += 1

        modules: List[Dict[str, str]] = []
        for mod, roles in sorted(module_roles.items()):
            dominant_role, _ = roles.most_common(1)[0]
            purpose_text = role_labels.get(dominant_role, "Module scaffold")
            modules.append({"name": mod, "purpose": purpose_text})
        return {"modules": modules}
    def _generate_module_structure(self, module: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        mod_files = []
        for f in files:
            p = f.get("path") or ""
            if not isinstance(p, str):
                continue
            if not p.startswith(f"{module}/"):
                continue
            mod_files.append({"path": p, "purpose": f.get("purpose", "")})
        mod_files = self._dedupe_files(mod_files)
        return {"module": module, "files": mod_files}

    def _merge_module_structures(self, module_structs: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged = []
        seen = set()
        for ms in module_structs:
            for f in ms.get("files", []) or []:
                path = f.get("path")
                if not isinstance(path, str) or not path:
                    continue
                if path in seen:
                    continue
                seen.add(path)
                merged.append({"path": path, "purpose": f.get("purpose", "")})
        merged.sort(key=lambda x: x.get("path") or "")
        validate_paths(merged)
        return {"files": merged}

    @staticmethod
    def _tokens_from_text(text: str) -> List[str]:
        raw = str(text or "").lower()
        return [token for token in re.split(r"[^a-z0-9]+", raw) if token]

    def _infer_file_role(self, path: str, purpose: str) -> str:
        path_tokens = self._tokens_from_text(path)
        purpose_tokens = self._tokens_from_text(purpose)
        tokens = set(path_tokens + purpose_tokens)
        role_order = [
            ("schema", {"schema", "schemas"}),
            ("model", {"model", "models", "entity", "entities", "dto", "type", "types"}),
            ("controller", {"controller", "controllers", "api", "endpoint", "endpoints", "route", "routes"}),
            ("service", {"service", "services", "usecase", "usecases"}),
            ("repository", {"repo", "repos", "repository", "repositories", "store"}),
            ("handler", {"handler", "handlers"}),
            ("utility", {"util", "utils", "helper", "helpers"}),
            ("config", {"config", "settings"}),
        ]
        for role, markers in role_order:
            if tokens.intersection(markers):
                return role
        if path.endswith(".py"):
            return "module"
        return "other"

    @staticmethod
    def _pascalize(value: str) -> str:
        parts = [p for p in str(value or "").replace("-", "_").split("_") if p]
        return "".join(part[:1].upper() + part[1:] for part in parts) or "Module"

    def _infer_type_name(self, file_path: str) -> str:
        stem = os.path.splitext(os.path.basename(file_path))[0]
        tokens = self._tokens_from_text(stem)
        suffixes = {
            "model",
            "schema",
            "dto",
            "entity",
            "type",
            "types",
            "request",
            "response",
            "payload",
            "service",
            "controller",
            "handler",
            "repository",
            "repo",
            "api",
        }
        while len(tokens) > 1 and tokens[-1] in suffixes:
            tokens.pop()
        base = "_".join(tokens) if tokens else stem
        return self._pascalize(base)

    @staticmethod
    def _singularize(token: str) -> str:
        value = str(token or "").strip().lower()
        if len(value) > 3 and value.endswith("ies"):
            return value[:-3] + "y"
        if len(value) > 2 and value.endswith("s") and not value.endswith("ss"):
            return value[:-1]
        return value

    def _infer_primary_domain(self, path: str, purpose: str, fallback: str) -> str:
        stop = {
            "api", "services", "service", "controller", "controllers", "model", "models", "schema", "schemas",
            "handler", "handlers", "repo", "repository", "repositories", "core", "app", "ui", "tests", "src",
            "crud", "http", "rest", "endpoint", "endpoints", "module", "utils", "util", "helper", "helpers",
            "request", "response", "payload", "entity", "entities", "type", "types", "domain", "operations",
        }
        candidates = self._tokens_from_text(path) + self._tokens_from_text(purpose)
        for token in candidates:
            singular = self._singularize(token)
            if singular and singular not in stop and len(singular) >= 3:
                return self._pascalize(singular)
        return fallback or "Domain"

    def _infer_type_candidates(self, path: str, purpose: str, role: str, type_name: str) -> List[Dict[str, str]]:
        tokens = set(self._tokens_from_text(path) + self._tokens_from_text(purpose))
        candidates: List[Dict[str, str]] = []
        if role in {"model", "schema"}:
            candidates.append({"name": type_name, "from": path, "kind": role})
        if role in {"service", "repository", "controller", "handler"}:
            if tokens.intersection({"request", "input", "command", "create", "update"}):
                candidates.append({"name": f"{type_name}Request", "from": path, "kind": "request"})
            if tokens.intersection({"response", "output", "view", "present", "api", "controller"}):
                candidates.append({"name": f"{type_name}Response", "from": path, "kind": "response"})
            if tokens.intersection({"search", "query", "filter", "list"}):
                candidates.append({"name": f"{type_name}Query", "from": path, "kind": "query"})
            if tokens.intersection({"auth", "login", "session", "token"}):
                candidates.append({"name": "AuthContext", "from": path, "kind": "auth"})
        return candidates

    def _infer_interface_methods(self, role: str, path: str, purpose: str) -> List[str]:
        tokens = set(self._tokens_from_text(path) + self._tokens_from_text(purpose))
        domain = self._infer_primary_domain(path, purpose, self._infer_type_name(path)).lower()
        plural_domain = f"{domain}s" if domain and not domain.endswith("s") else domain
        methods: List[str] = []
        if role in {"service", "repository"}:
            methods.extend([
                f"create_{domain}",
                f"get_{domain}",
                f"list_{plural_domain}",
                f"update_{domain}",
                f"delete_{domain}",
            ])
            if tokens.intersection({"search", "query", "filter"}):
                methods.append(f"search_{plural_domain}")
            if tokens.intersection({"auth", "login", "session", "token"}):
                methods.extend(["authenticate", "issue_token", "revoke_token"])
        elif role == "controller":
            methods.extend([
                f"get_{domain}",
                f"list_{plural_domain}",
                f"create_{domain}",
                f"update_{domain}",
                f"delete_{domain}",
            ])
        elif role == "handler":
            methods.extend(["handle", "validate_input", "map_result"])
            if tokens.intersection({"event", "message", "job", "queue"}):
                methods.append("dispatch")
        elif role == "module":
            methods.extend(["run"])

        deduped: List[str] = []
        seen = set()
        for method in methods:
            if method and method not in seen:
                seen.add(method)
                deduped.append(method)
        return deduped

    def _build_context_bundle(self, plan: Dict[str, Any], structure: Dict[str, Any]) -> Dict[str, Any]:
        modules: List[Dict[str, Any]] = list(plan.get("modules") or [])
        files = [f for f in (structure.get("files") or []) if isinstance(f, dict)]
        # ------------------------------------------------------------------ #
        # Infer shared types and public interfaces
        # ------------------------------------------------------------------ #
        shared_types: List[Dict[str, str]] = []
        public_interfaces: List[Dict[str, Any]] = []
        type_modules_map: Dict[Tuple[str, str], set] = collections.defaultdict(set)
        seen_types = set()
        seen_interfaces = set()

        for item in sorted(files, key=lambda row: str(row.get("path") or "")):
            path = str(item.get("path") or "").strip()
            purpose = str(item.get("purpose") or "").strip()
            if not path:
                continue
            role = self._infer_file_role(path, purpose)
            type_name = self._infer_primary_domain(path, purpose, self._infer_type_name(path))
            module_name = path.split("/")[0] if "/" in path else ""

            for inferred_type in self._infer_type_candidates(path, purpose, role, type_name):
                key = (inferred_type.get("name"), inferred_type.get("kind"))
                type_modules_map[key].add(module_name)
                if key in seen_types:
                    continue
                seen_types.add(key)
                shared_types.append(inferred_type)

            if role in {"service", "repository", "controller", "handler"}:
                suffix = {
                    "service": "Service",
                    "repository": "Repository",
                    "controller": "Controller",
                    "handler": "Handler",
                }.get(role, "Interface")
                iface_name = f"{type_name}{suffix}"
                interface_key = (iface_name, path)
                if interface_key in seen_interfaces:
                    continue
                seen_interfaces.add(interface_key)
                public_interfaces.append(
                    {
                        "name": iface_name,
                        "file": path,
                        "role": role,
                        "methods": self._infer_interface_methods(role, path, purpose),
                    }
                )

        # Keep only types that appear in 2+ distinct modules
        filtered_shared_types = [
            t for t in shared_types if len(type_modules_map[(t.get("name"), t.get("kind"))]) >= 2
        ] or shared_types  # fallback to all if nothing qualifies

        # ------------------------------------------------------------------ #
        # Project summary – short and deterministic
        # ------------------------------------------------------------------ #
        module_names = [m["name"] for m in modules if isinstance(m, dict) and "name" in m]
        summary_parts = [
            f"{len(module_names)} module{'s' if len(module_names)!=1 else ''}: {', '.join(module_names[:5])}{'…' if len(module_names)>5 else ''}",
            f"{len(files)} Python file{'s' if len(files)!=1 else ''}",
        ]
        if filtered_shared_types:
            top_types = [t["name"] for t in filtered_shared_types[:3]]
            summary_parts.append(f"key domain types – {', '.join(top_types)}")
        summary = "; ".join(summary_parts)

        coding_rules = [
            # existing
            "Return complete files only (no diffs or patches).",
            "Preserve stable imports, module boundaries, and naming conventions.",
            "Use shared/domain types from context_bundle and keep interfaces consistent.",
            "Implement concise docstrings and validations for public entry points.",
            # new
            "Maintain full‑file generation contract and interface_contracts.json alignment.",
            "Do not introduce new external APIs unless strictly required.",
        ]
        return {
            "project_summary": summary,
            "modules": modules,
            "shared_types": filtered_shared_types,
            "public_interfaces": public_interfaces,
            "coding_rules": coding_rules,
        }
    def _build_interface_contracts(self, structure: Dict[str, Any]) -> Dict[str, Any]:
        contracts: List[Dict[str, Any]] = []
        inferred_types: List[Dict[str, str]] = []
        seen_types = set()

        for item in sorted(structure.get("files", []) or [], key=lambda row: str((row or {}).get("path") or "")):
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            purpose = str(item.get("purpose") or "")
            if not isinstance(path, str) or not path.endswith(".py"):
                continue

            role = self._infer_file_role(path, purpose)
            type_name = self._infer_primary_domain(path, purpose, self._infer_type_name(path))

            for inferred_type in self._infer_type_candidates(path, purpose, role, type_name):
                type_key = (inferred_type.get("name"), inferred_type.get("kind"))
                if type_key in seen_types:
                    continue
                seen_types.add(type_key)
                inferred_types.append(inferred_type)

            if role in {"model", "schema", "other"}:
                continue

            class_suffix = {
                "service": "Service",
                "repository": "Repository",
                "controller": "Controller",
                "handler": "Handler",
                "module": "Module",
                "utility": "Utility",
                "config": "Config",
            }.get(role, "Module")
            class_name = f"{type_name}{class_suffix}"
            methods = self._infer_interface_methods(role, path, purpose)
            contracts.append({"file": path, "class": class_name, "methods": methods})

        return {"interfaces": contracts, "types": inferred_types}

    def _create_a3_handoff_artifacts(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        modules_plan = parse_json_strict(json.dumps(self._generate_modules_plan(files), ensure_ascii=False))
        self._save_json_artifact("modules", modules_plan)

        module_structs: List[Dict[str, Any]] = []
        for mod in modules_plan.get("modules", []) or []:
            mod_name = str(mod.get("name") or "").strip()
            if not mod_name:
                continue
            mod_struct = parse_json_strict(json.dumps(self._generate_module_structure(mod_name, files), ensure_ascii=False))
            module_structs.append(mod_struct)
            self._save_json_artifact(f"modules/{mod_name}", mod_struct)

        structure_artifact = parse_json_strict(json.dumps(self._merge_module_structures(module_structs), ensure_ascii=False))
        validate_paths(structure_artifact.get("files") or [])
        self._save_json_artifact("structure", structure_artifact)

        context_bundle = parse_json_strict(json.dumps(self._build_context_bundle(modules_plan, structure_artifact), ensure_ascii=False))
        self._save_json_artifact("context_bundle", context_bundle)

        contracts_bundle = parse_json_strict(json.dumps(self._build_interface_contracts(structure_artifact), ensure_ascii=False))
        self._save_json_artifact("interface_contracts", contracts_bundle)

        return {
            "modules": modules_plan,
            "structure": structure_artifact,
            "context": context_bundle,
            "contracts": contracts_bundle,
        }

    def _load_a3_handoff_artifacts(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        paths = {
            "modules": os.path.join(self.log.paths.responses_dir, "modules.json"),
            "structure": os.path.join(self.log.paths.responses_dir, "structure.json"),
            "context": os.path.join(self.log.paths.responses_dir, "context_bundle.json"),
            "contracts": os.path.join(self.log.paths.responses_dir, "interface_contracts.json"),
        }
        for key, p in paths.items():
            if os.path.isfile(p):
                obj = self._load_json_artifact(p)
                if obj is not None:
                    out[key] = obj
        return out

    def _choose_a3_generation_mode(self, artifacts: Dict[str, Any]) -> str:
        if all(k in artifacts for k in ("structure", "context", "contracts")):
            return "handoff"
        return "prev_chain"

    def _generate_full_file(
        self,
        client: OpenAIClient,
        file_spec: Dict[str, Any],
        diag_file_ids: List[str],
        context_bundle: Dict[str, Any],
        contracts_bundle: Dict[str, Any],
        structure_map: Dict[str, Dict[str, Any]],
    ) -> Tuple[str, Optional[str], Optional[str], str, str]:
        path = str(file_spec.get("path") or "").strip()
        purpose = str(file_spec.get("purpose") or "")
        if not path:
            return "", None, "missing path", "", purpose
        try:
            context_hint = {
                "file": path,
                "purpose": purpose,
                "module_context": structure_map.get(path, {}),
                "context_bundle": context_bundle,
                "interface_contracts": contracts_bundle,
            }
            extra_prompt = f"A3 handoff context: {json.dumps(context_hint, ensure_ascii=False)}"
            content, last_resp = self._gen_file_chunks(
                client,
                prev_id="",
                contract="A3_FILE",
                path=path,
                action=None,
                diag_file_ids=diag_file_ids,
                tools=self._fs_tools,
                extra_prompt=extra_prompt,
            )
            return content, last_resp, None, path, purpose
        except Exception as e:
            return "", None, str(e), path, purpose

    def _generate_full_files_parallel(
        self,
        client: OpenAIClient,
        files: List[Dict[str, Any]],
        diag_file_ids: List[str],
        context_bundle: Dict[str, Any],
        contracts_bundle: Dict[str, Any],
        structure: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        results: List[Dict[str, Any]] = []
        errors: List[str] = []
        if not files:
            return results, errors

        structure_map: Dict[str, Dict[str, Any]] = {}
        for item in structure.get("files", []) or []:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if isinstance(path, str) and path:
                structure_map[path] = item

        max_workers = min(6, len(files))
        self._log_debug(f"A3 parallel workers: {max_workers}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_map = {}
            for spec in files:
                path = spec.get("path")
                if not isinstance(path, str) or not path:
                    continue
                self._log_debug(f"A3_FILE_START {path}")
                fut = ex.submit(
                    self._generate_full_file,
                    client,
                    spec,
                    diag_file_ids,
                    context_bundle,
                    contracts_bundle,
                    structure_map,
                )
                fut_map[fut] = path
            for fut in concurrent.futures.as_completed(fut_map):
                path = fut_map[fut]
                try:
                    content, _last_resp, err, result_path, purpose = fut.result()
                    used_path = result_path or path
                    if err:
                        errors.append(f"{used_path}: {err}")
                        self._log_debug(f"A3_FILE_ERROR {used_path} {err}")
                    else:
                        results.append({"path": used_path, "content": content, "purpose": purpose})
                        self._log_debug(f"A3_FILE_DONE {used_path}")
                except Exception as e:
                    errors.append(f"{path}: {e}")
                    self._log_debug(f"A3_FILE_ERROR {path} {e}")
        results.sort(key=lambda item: item.get("path") or "")
        return results, errors

    def _remember_file_name(self, file_id: str, name: str) -> None:
        fid = str(file_id or "").strip()
        if not fid:
            return
        filename = str(name or "").strip()
        if filename:
            self._file_name_cache[fid] = filename
        ext = os.path.splitext(filename)[1].lower()
        if ext in SUPPORTED_INPUT_IMAGE_EXTS:
            self._input_kind_cache[fid] = "input_image"
        elif ext in SUPPORTED_INPUT_FILE_EXTS:
            self._input_kind_cache[fid] = "input_file"
        else:
            self._input_kind_cache[fid] = "unsupported"

    def _classify_input_kind(self, client: OpenAIClient, file_id: str) -> str:
        fid = str(file_id or "").strip()
        if not fid:
            return "unsupported"
        cached = self._input_kind_cache.get(fid)
        if cached:
            return cached
        filename = self._file_name_cache.get(fid, "")
        if filename:
            self._remember_file_name(fid, filename)
            return self._input_kind_cache.get(fid, "unsupported")
        try:
            meta = with_retry(lambda f=fid: client.retrieve_file(f), self.settings.retry, self.breaker)
            filename = str(meta.get("filename") or "").strip()
            self._remember_file_name(fid, filename)
        except Exception as e:
            self._input_kind_cache[fid] = "unsupported"
            self._log_debug(f"input-kind lookup failed for {fid}; skipping from input ({e})")
            return "unsupported"
        kind = self._input_kind_cache.get(fid, "unsupported")
        if kind == "unsupported":
            shown = filename or "(unknown filename)"
            self._log_debug(f"Unsupported input attachment type for {fid} ({shown}); keeping only reference.")
        return kind

    def _log_request_attachments(
        self,
        stage: str,
        ref_file_ids: List[str],
        input_file_ids: List[str],
        input_image_ids: List[str],
        vector_store_ids: List[str],
        tools: Optional[List[Dict[str, Any]]],
    ) -> None:
        snapshot = self._attachments_snapshot(stage, ref_file_ids, input_file_ids, input_image_ids, vector_store_ids, tools)
        try:
            self.log.event("request.attachments", snapshot)
        except Exception:
            pass
        if snapshot.get("file_ids") or snapshot.get("input_file_ids") or snapshot.get("input_image_ids") or snapshot.get("vector_store_ids") or snapshot.get("tool_types"):
            self._log_debug(
                f"{stage}: attachments files={len(snapshot.get('file_ids') or [])} input_files={len(snapshot.get('input_file_ids') or [])} "
                f"input_images={len(snapshot.get('input_image_ids') or [])} vector_stores={len(snapshot.get('vector_store_ids') or [])} "
                f"tools={','.join(snapshot.get('tool_types') or []) or 'none'}"
            )

    def request_stop(self):
        self._stop = True

    def _check_stop(self):
        if self._stop:
            raise RuntimeError("STOP_REQUESTED")

    def _set(self, p: int, sp: int, msg: str):
        self.progress.emit(p)
        self.subprogress.emit(sp)
        self.status.emit(msg)
        self._log_debug(msg)
        try:
            self.log.event("ui.progress", {"p": p, "sp": sp, "msg": msg, "ts": self._ts()})
        except Exception:
            pass

    def run(self):
        try:
            self.log.update_state(
                {
                    "status": "running",
                    "started_at": time.time(),
                    "mode": self.cfg.mode,
                    "send_as_c": self.cfg.send_as_c,
                    "model": self.cfg.model_default,
                    "model_default": self.cfg.model_default,
                    "model_a1": self.cfg.model_a1,
                    "model_a2": self.cfg.model_a2,
                    "model_a3": self.cfg.model_a3,
                    "out_dir": self.cfg.out_dir,
                }
            )
            client = OpenAIClient(self.api_key)
            stage_caps = "A1" if self.cfg.mode == "GENERATE" else ("B1" if self.cfg.mode == "MODIFY" else self.cfg.mode)

            if self.cfg.mode == "QFILE" and self.cfg.send_as_c:
                raise RuntimeError("QFILE nepodporuje SEND AS BATCH.")

            # Generate cascade requires previous_response_id in A1/A2/A3 chaining.
            if (not self.cfg.send_as_c) and self.cfg.mode == "GENERATE":
                for stage in ("A1", "A2", "A3"):
                    caps = self._caps_for_step(stage)
                    if caps.get("supports_previous_response_id") is False:
                        raise RuntimeError(f"Model for {stage} does not support previous_response_id")
            # Modify cascade requires chaining as well (single model path).
            if (not self.cfg.send_as_c) and self.cfg.mode == "MODIFY":
                caps = self._caps_for_step("B1")
                if caps.get("supports_previous_response_id") is False:
                    raise RuntimeError("Model for MODIFY does not support previous_response_id")

            diag_file_ids, diag_text = self._maybe_collect_diagnostics(client)
            self._diag_text = diag_text or ""
            self._in_dir_info = self._prepare_in_dir_upload(client)
            self._vector_store_ids = list(self.cfg.attached_vector_store_ids or [])
            if self._in_dir_info and self._in_dir_info.get("vector_store_id"):
                self._vector_store_ids.append(str(self._in_dir_info["vector_store_id"]))
            if diag_file_ids:
                self._attach_diagnostics_vector_store(client, diag_file_ids)
            caps_main = self._caps_for_step(stage_caps)
            if bool(caps_main.get("supports_file_search", False)) and (bool(self.cfg.use_file_search) or bool(diag_file_ids)) and self._vector_store_ids:
                uniq: List[str] = []
                seen: set = set()
                for vid in self._vector_store_ids:
                    if vid and vid not in seen:
                        uniq.append(vid)
                        seen.add(vid)
                if uniq:
                    self._fs_tools = [{"type": "file_search", "vector_store_ids": uniq}]
            try:
                all_file_ids = list(self.cfg.attached_file_ids or [])
                input_file_ids, input_image_ids = self._build_input_attachments(client, self._input_file_ids())
                zip_supported = bool(self._diag_zip_path and self._is_supported_input_file(self._diag_zip_path))
                supports_input_file = bool(caps_main.get("supports_input_file", True))
                supports_vector_store = bool(caps_main.get("supports_vector_store", False))
                self.log.event(
                    "io.reference",
                    {
                        "file_ids": all_file_ids,
                        "input_file_ids": list(input_file_ids),
                        "input_image_ids": list(input_image_ids),
                        "vector_store_ids": list(self._vector_store_ids or []),
                        "use_file_search": bool(self.cfg.use_file_search),
                        "supports_file_search": bool(caps_main.get("supports_file_search", False)),
                        "supports_vector_store": bool(caps_main.get("supports_vector_store", False)),
                        "supports_input_file": supports_input_file,
                        "diagnostics_zip": self._diag_zip_path or None,
                        "diagnostics_zip_supported_input": zip_supported,
                        "file_search": bool(self._fs_tools),
                    },
                )
            except Exception:
                pass

            pricing_updated = self._refresh_pricing_via_model(client)

            # LONG PROMPT handling:
# - GENERATE/MODIFY: explicit ingest cascade A0 (keeps continuity via previous_response_id)
# - QA/BATCH: no ingest; prompt is sent as chunked message parts
            if self.cfg.send_as_c:
                base_prev_id = None
            elif self.cfg.mode in ("GENERATE", "MODIFY"):
                base_prev_id = self._ingest_prompt_if_needed(client, prev_id=self.cfg.response_id or None)
            else:
                base_prev_id = self.cfg.response_id or None


            if self.cfg.send_as_c:
                result = self._run_c_batch(client, diag_file_ids, base_prev_id)
            else:
                if self.cfg.mode == "GENERATE":
                    result = self._run_a_generate(client, diag_file_ids, base_prev_id)
                elif self.cfg.mode == "MODIFY":
                    result = self._run_b_modify(client, diag_file_ids, base_prev_id)
                elif self.cfg.mode == "QA":
                    result = self._run_qa(client, diag_file_ids, base_prev_id)
                elif self.cfg.mode == "QFILE":
                    result = self._run_qfile(client, diag_file_ids, base_prev_id)
                else:
                    raise RuntimeError(f"Unknown mode: {self.cfg.mode}")
            if self._final_response_id and not result.get("response_id"):
                result["response_id"] = self._final_response_id
            result["pricing_snapshot"] = pricing_updated

            self.log.update_state({"status": "completed", "completed_at": time.time()})
            self.finished_ok.emit(result)
        except BaseException as e:
            msg = str(e)
            if self._last_prev_id_error:
                msg = self._last_prev_id_error
            if str(e) == "STOP_REQUESTED":
                self.log.update_state({"status": "stopped", "stopped_at": time.time()})
                try:
                    self._ensure_receipt_on_failure("stopped_by_user", flow_type="RUN_STOPPED")
                except Exception:
                    pass
                self.finished_err.emit("STOPPED")
            else:
                try:
                    self.log.exception("run", e)
                except Exception:
                    pass
                self.log.update_state({"status": "failed", "failed_at": time.time(), "error": str(e)})
                try:
                    self._ensure_receipt_on_failure(f"failed: {msg}", flow_type="RUN_FAILED")
                except Exception:
                    pass
                self.finished_err.emit(msg)

    # ---------- payload helpers ----------
    def _input_parts(self, text: str, file_ids: List[str], image_file_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Build Responses API input using message/content with input_text + optional input_file/input_image parts."""
        chunks = split_text(text, max_chars=20_000)
        if not chunks:
            chunks = [""]
        parts: List[Dict[str, Any]] = []
        image_ids = [fid for fid in (image_file_ids or []) if fid]
        for i, ch in enumerate(chunks):
            content: List[Dict[str, Any]] = [{"type": "input_text", "text": ch}]
            if i == 0 and file_ids:
                for fid in file_ids:
                    content.append({"type": "input_file", "file_id": fid})
            if i == 0 and image_ids:
                for fid in image_ids:
                    content.append({"type": "input_image", "file_id": fid})
            parts.append({"type": "message", "role": "user", "content": content})
        return parts

    def _payload_base(
        self,
        stage: str,
        instructions: str,
        input_parts: List[Dict[str, Any]],
        prev_id: Optional[str],
    ) -> Dict[str, Any]:
        model = self._generate_model(stage)
        caps = self._caps_for_step(stage)
        payload: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_parts,
        }
        if bool(caps.get("supports_temperature", True)):
            payload["temperature"] = float(self.cfg.temperature)
        if prev_id and bool(caps.get("supports_previous_response_id", True)):
            payload["previous_response_id"] = prev_id
        return payload

    # ---------- diagnostics ----------
    def _build_diag_text(self, files: List[str]) -> str:
        allowed_exts = {
            ".txt", ".log", ".json", ".xml", ".yaml", ".yml", ".md", ".csv",
            ".ini", ".cfg", ".conf", ".ps1", ".bat", ".cmd", ".sh"
        }
        max_total = 120_000
        max_per_file = 20_000
        parts: List[str] = []
        total = 0
        for fp in files:
            if total >= max_total:
                break
            ext = os.path.splitext(fp)[1].lower()
            if ext and ext not in allowed_exts:
                continue
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(max_per_file)
            except Exception:
                continue
            if not content.strip():
                continue
            header = f"\n# {os.path.basename(fp)}\n"
            if total + len(header) >= max_total:
                break
            parts.append(header)
            total += len(header)
            if total + len(content) > max_total:
                content = content[: max_total - total]
            parts.append(content)
            total += len(content)
        return "".join(parts).strip()

    def _write_diagnostics_json(self, root: str, files: List[str]) -> Optional[str]:
        if not root or not os.path.isdir(root):
            return None
        ensure_dir(self.log.paths.files_dir)
        json_path = os.path.join(self.log.paths.files_dir, f"diagnostics_{ts_code()}.json")
        try:
            text_exts = {
                ".txt", ".log", ".json", ".xml", ".yaml", ".yml", ".md", ".csv",
                ".ini", ".cfg", ".conf", ".ps1", ".bat", ".cmd", ".sh", ".reg"
            }
            total_size = 0
            payload: Dict[str, Any] = {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source_root": root,
                "file_count": 0,
                "total_size_bytes": 0,
                "files": [],
            }
            for fp in files:
                rel = os.path.relpath(fp, root)
                ext = os.path.splitext(fp)[1].lower()
                try:
                    size = os.path.getsize(fp)
                except Exception:
                    size = None
                try:
                    if ext in text_exts:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        payload["files"].append({"path": rel, "encoding": "utf-8", "content": content, "bytes": size})
                    else:
                        with open(fp, "rb") as f:
                            data = f.read()
                        b64 = base64.b64encode(data).decode("ascii")
                        payload["files"].append({"path": rel, "encoding": "base64", "content": b64, "bytes": size})
                    payload["file_count"] += 1
                    if size:
                        total_size += int(size)
                except Exception:
                    continue
            payload["total_size_bytes"] = total_size
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return json_path
        except Exception:
            return None

    def _maybe_collect_diagnostics(self, client: OpenAIClient) -> Tuple[List[str], str]:
        diag_file_ids: List[str] = []
        diag_text = ""
        if not (self.cfg.diag_windows_in or self.cfg.diag_ssh_in):
            return diag_file_ids, diag_text

        self._set(2, 0, "Diagnostics IN: collecting...")
        diag_root = os.path.join(self.log.paths.manifests_dir, "diagnostics")
        ensure_dir(diag_root)
        diag_files: List[str] = []

        if self.cfg.diag_windows_in:
            from .diagnostics.windows import collect_windows_diagnostics
            try:
                self._log_debug("Diagnostics IN: Windows collect...")
                folder, files = collect_windows_diagnostics(diag_root, on_line=self._log_debug)
                diag_files.extend(files)
                try:
                    self.log.event("diagnostics.windows.collected", {"folder": folder, "count": len(files)})
                except Exception:
                    pass
            except Exception as e:
                try:
                    self.log.exception("diagnostics.windows.failed", e)
                except Exception:
                    pass
                raise RuntimeError(f"Diagnostics Windows failed: {e}")

        if self.cfg.diag_ssh_in:
            from .diagnostics.ssh import collect_ssh_diagnostics
            if not (self.cfg.ssh_host and self.cfg.ssh_user):
                raise RuntimeError("Diagnostics SSH failed: missing SSH host/user.")
            try:
                self._log_debug("Diagnostics IN: SSH collect...")
                folder, files = collect_ssh_diagnostics(
                    diag_root,
                    self.cfg.ssh_host,
                    self.cfg.ssh_user,
                    self.cfg.ssh_key,
                    self.cfg.ssh_password,
                    on_line=self._log_debug,
                    timeout_s=900,
                )
                diag_files.extend(files)
                try:
                    self.log.event("diagnostics.ssh.collected", {"folder": folder, "count": len(files)})
                except Exception:
                    pass
            except Exception as e:
                try:
                    self.log.exception("diagnostics.ssh.failed", e)
                except Exception:
                    pass
                raise RuntimeError(f"Diagnostics SSH failed: {e}")

        diag_text = self._build_diag_text(diag_files)

        self._log_debug("Diagnostics IN: write JSON bundle...")
        json_path = self._write_diagnostics_json(diag_root, diag_files)
        self._diag_zip_path = json_path or ""
        if json_path:
            try:
                self._log_debug("Diagnostics IN: upload JSON to Files API...")
                up = with_retry(lambda p=json_path: client.upload_file(p, purpose="user_data"), self.settings.retry, self.breaker)
                diag_file_ids.append(up["id"])
                self._remember_file_name(up["id"], os.path.basename(json_path))
                self.log.event("upload.diagnostics", {"local": json_path, "file_id": up["id"], "purpose": "user_data", "bytes": os.path.getsize(json_path)})
            except Exception as e:
                try:
                    self.log.exception("upload.diagnostics", e)
                except Exception:
                    pass
        return diag_file_ids, diag_text

    def _zip_in_dir(self, root: str) -> str:
        root = os.path.abspath(root)
        ensure_dir(self.log.paths.files_dir)
        zip_path = os.path.join(self.log.paths.files_dir, f"in_dir_{ts_code()}.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in ("venv", ".venv", "LOG")]
                for fname in filenames:
                    abs_path = os.path.join(dirpath, fname)
                    rel = os.path.relpath(abs_path, root)
                    try:
                        zf.write(abs_path, rel)
                    except Exception:
                        try:
                            self.log.event("zip.skip", {"path": abs_path})
                        except Exception:
                            pass
        return zip_path

    def _prepare_in_dir_upload(self, client: OpenAIClient) -> Optional[Dict[str, Any]]:
        in_dir = (self.cfg.in_dir or "").strip()
        if not in_dir or not os.path.isdir(in_dir):
            return None
        stage_caps = "A1" if self.cfg.mode == "GENERATE" else ("B1" if self.cfg.mode == "MODIFY" else self.cfg.mode)
        caps = self._caps_for_step(stage_caps)
        self._set(4, 0, "IN: zipping + upload...")
        zip_path = self._zip_in_dir(in_dir)
        up = with_retry(lambda: client.upload_file(zip_path, purpose="user_data"), self.settings.retry, self.breaker)
        file_id = up["id"]
        self._remember_file_name(file_id, os.path.basename(zip_path))
        info: Dict[str, Any] = {"zip_path": zip_path, "file_id": file_id, "vector_store_id": None}
        try:
            self.log.event("upload.in_dir", {"zip": zip_path, "file_id": file_id, "bytes": os.path.getsize(zip_path)})
        except Exception:
            pass

        if bool(caps.get("supports_vector_store", False)):
            try:
                self._set(6, 0, "IN: vytvářím vector store z archivu...")
                vs = with_retry(lambda: client.create_vector_store(f"IN_{ts_code()}"), self.settings.retry, self.breaker)
                vs_id = vs.get("id")
                if vs_id:
                    vs_file = with_retry(lambda: client.add_file_to_vector_store(vs_id, file_id), self.settings.retry, self.breaker)
                    vs_file_id = str(vs_file.get("id") or "")
                    if vs_file_id:
                        self._wait_vector_store_files(client, vs_id, [vs_file_id])
                    info["vector_store_id"] = vs_id
                    try:
                        self.log.event("vector_store.in_dir", {"vector_store_id": vs_id, "file_id": file_id})
                    except Exception:
                        pass
            except Exception as e:
                try:
                    self.log.exception("vector_store.in_dir", e)
                except Exception:
                    pass
        return info

    def _files_with_in_dir(self, file_ids: List[str]) -> List[str]:
        ids = list(file_ids or [])
        fid = None
        try:
            fid = self._in_dir_info.get("file_id") if self._in_dir_info else None
        except Exception:
            fid = None
        if fid and fid not in ids:
            ids.append(fid)
        return ids

    def _is_supported_input_file(self, path: str) -> bool:
        ext = os.path.splitext(path or "")[1].lower()
        return bool(ext and ext in SUPPORTED_INPUT_FILE_EXTS)

    def _input_files_with_in_dir(self, file_ids: List[str]) -> List[str]:
        ids = list(file_ids or [])
        fid = None
        zpath = ""
        try:
            fid = self._in_dir_info.get("file_id") if self._in_dir_info else None
            zpath = str(self._in_dir_info.get("zip_path") or "") if self._in_dir_info else ""
        except Exception:
            fid = None
            zpath = ""
        if fid and fid not in ids:
            if zpath and self._is_supported_input_file(zpath):
                ids.append(fid)
            else:
                self._log_debug("IN: ZIP není podporovaný input_file; přeskočeno v input.")
        return ids

    def _build_input_attachments(self, client: OpenAIClient, base_ids: List[str]) -> Tuple[List[str], List[str]]:
        candidate_ids = self._input_files_with_in_dir(base_ids)
        file_ids: List[str] = []
        image_ids: List[str] = []
        seen: set = set()
        for fid in candidate_ids:
            fid_s = str(fid or "").strip()
            if not fid_s or fid_s in seen:
                continue
            seen.add(fid_s)
            kind = self._classify_input_kind(client, fid_s)
            if kind == "input_image":
                image_ids.append(fid_s)
            elif kind == "input_file":
                file_ids.append(fid_s)
        return file_ids, image_ids

    def _io_reference_note(self, file_ids: List[str]) -> str:
        ids = [fid for fid in (file_ids or []) if fid]
        vs_ids = [vid for vid in (self._vector_store_ids or []) if vid]
        if not ids and not vs_ids:
            return ""
        parts: List[str] = ["DATA REFERENCE:"]
        if ids:
            parts.append(f"Files API file_id: {', '.join(ids)}")
            parts.append("Pouzij soubory v inputu automaticky podle typu a podporovaneho formatu: dokumenty jako input_file, obrazky (napr. PNG/JPG/WEBP/GIF) jako input_image.")
        if vs_ids:
            parts.append(f"Vector store id: {', '.join(vs_ids)}")
            parts.append("Pokud model podporuje file_search, pouzij file_search nad uvedenymi vector store.")
        return "\n".join(parts)

    def _append_io_reference(self, text: str, file_ids: List[str]) -> str:
        note = self._io_reference_note(file_ids)
        if not note:
            return text
        if note in text:
            return text
        return f"{text}\n\n{note}"

    def _append_io_reference_instructions(self, instructions: str, file_ids: List[str]) -> str:
        note = self._io_reference_note(file_ids)
        if not note:
            return instructions
        if note in instructions:
            return instructions
        return f"{instructions}\n\n{note}"

    def _should_inline_diag_text(self) -> bool:
        return False

    def _attach_diagnostics_vector_store(self, client: OpenAIClient, diag_file_ids: List[str]) -> None:
        if not diag_file_ids:
            return
        stage_caps = "A1" if self.cfg.mode == "GENERATE" else ("B1" if self.cfg.mode == "MODIFY" else self.cfg.mode)
        caps = self._caps_for_step(stage_caps)
        supports_vs = bool(caps.get("supports_vector_store", False))
        supports_fs = bool(caps.get("supports_file_search", False))
        if not (supports_vs and supports_fs):
            raise RuntimeError("Diagnostics IN vyžaduje model s podporou vector store + file_search.")
        self._log_debug("Diagnostics IN: create vector store...")
        vs = with_retry(lambda: client.create_vector_store(f"DIAG_{ts_code()}"), self.settings.retry, self.breaker)
        vs_id = str(vs.get("id") or "")
        if not vs_id:
            raise RuntimeError("Diagnostics IN: nepodařilo se vytvořit vector store.")
        self._log_debug("Diagnostics IN: add JSON file_id to vector store...")
        vs_file_ids: List[str] = []
        for fid in diag_file_ids:
            if not fid:
                continue
            vs_file = with_retry(lambda v=vs_id, f=fid: client.add_file_to_vector_store(v, f), self.settings.retry, self.breaker)
            vs_file_id = str(vs_file.get("id") or "")
            if vs_file_id:
                vs_file_ids.append(vs_file_id)
        if vs_file_ids:
            self._log_debug("Diagnostics IN: wait for vector store indexing...")
            self._wait_vector_store_files(client, vs_id, vs_file_ids)
        self._diag_vector_store_ids.append(vs_id)
        self._vector_store_ids.append(vs_id)
        self._log_debug(f"Diagnostics: vector store {vs_id} attached.")

    def _with_diag_text(self, text: str) -> str:
        if not self._should_inline_diag_text():
            return text
        if "DIAGNOSTICS (PARSED):" in text:
            return text
        return f"{text}\n\nDIAGNOSTICS (PARSED):\n{self._diag_text}"

    def _wait_vector_store_files(self, client: OpenAIClient, vs_id: str, vs_file_ids: List[str], timeout_s: int = 180) -> None:
        if not vs_file_ids:
            return
        start = time.time()
        pending = set(vs_file_ids)
        while pending:
            self._check_stop()
            if time.time() - start > timeout_s:
                raise RuntimeError(f"Vector store index timeout ({vs_id}).")
            completed: List[str] = []
            for vs_file_id in list(pending):
                try:
                    info = with_retry(lambda v=vs_id, f=vs_file_id: client.retrieve_vector_store_file(v, f), self.settings.retry, self.breaker)
                except Exception:
                    continue
                status = str(info.get("status") or "")
                if status == "completed":
                    completed.append(vs_file_id)
                elif status == "failed":
                    last_error = info.get("last_error") or {}
                    msg = last_error.get("message") or "Vector store indexing failed."
                    raise RuntimeError(f"Vector store indexing failed ({vs_id}): {msg}")
            for done in completed:
                pending.discard(done)
            if pending:
                time.sleep(2.0)


    def _in_dir_fallback_note(self) -> str:
        if not self._in_dir_info or not self._in_dir_info.get("file_id"):
            return ""
        stage_caps = "A1" if self.cfg.mode == "GENERATE" else ("B1" if self.cfg.mode == "MODIFY" else self.cfg.mode)
        caps = self._caps_for_step(stage_caps)
        supports_fs = bool(caps.get("supports_file_search", False))
        supports_vs = bool(caps.get("supports_vector_store", False))
        if supports_fs or supports_vs:
            return ""
        return f"IN adresář je nahrán jako ZIP na Files API (file_id={self._in_dir_info['file_id']}). Model nemá file_search ani vector store; použij tento soubor jako zdroj dat."

    def _refresh_pricing_via_model(self, client: OpenAIClient) -> bool:
        if not self.api_key:
            return False
        try:
            resp = with_retry(lambda: client.create_response(PricingFetcher.payload()), self.settings.retry, self.breaker)
            rows = PricingFetcher.parse_response(resp)
            if rows:
                self.price_table.update_from_rows(rows, verified=False)
                try:
                    self.price_table.save_cache()
                except Exception:
                    pass
                self.log.event("pricing.model_refresh", {"model": PricingFetcher.DEFAULT_MODEL, "count": len(rows)})
                return True
        except Exception as exc:
            try:
                self.log.event("pricing.model_refresh_failed", {"error": str(exc)})
            except Exception:
                pass
        return False

    # ---------- long prompt ingest ----------
    def _ingest_prompt_if_needed(self, client: OpenAIClient, prev_id: Optional[str]) -> Optional[str]:
        prompt = self.cfg.prompt or ""
        if len(prompt) <= 150_000:
            return prev_id

        # Must have chaining for ingest
        if self._caps_for_step("A0").get("supports_previous_response_id") is False:
            raise RuntimeError("Long prompt ingest requires previous_response_id (model flagged as unsupported).")

        self._set(4, 0, f"A0: ingest long prompt ({len(prompt)} chars) ...")
        chunks = split_text(prompt, max_chars=20_000)
        part_count = len(chunks)

        last_id = prev_id
        for i, ch in enumerate(chunks):
            self._check_stop()
            self.subprogress.emit(int((i + 1) * 100 / max(1, part_count)))

            schema = '{"contract":"A0_INGEST_ACK","part_index":0,"part_count":0,"ok":true}'
            instructions = (
                "You are an ingestion step. DO NOT summarize. "
                "Return ONLY valid JSON matching contract. No extra text. "
                f"CONTRACT: {schema}"
            )
            payload = self._payload_base(
                stage="A0",
                instructions=instructions,
                input_parts=self._input_parts(f"PART {i+1}/{part_count}:\n{ch}", []),
                prev_id=last_id,
            )
            self.log.save_json(
                "requests",
                f"A0_ingest_{i}_{ts_code()}",
                {
                    "payload": payload,
                    "ui_state": self._ui_state_for_log(),
                    **self._request_model_fields(),
                },
            )
            resp = with_retry(lambda: client.create_response(payload), self.settings.retry, self.breaker)
            self.log.save_json("responses", f"A0_ingest_resp_{resp.get('id','NOID')}_{i}_{ts_code()}", resp)

            last_id = str(resp.get("id") or "")
            if not last_id:
                raise RuntimeError("A0 ingest: missing response id")

        self._set(6, 100, f"A0: ingest done, base_prev_id={last_id}")
        return last_id

    # ---------- versing + write ----------
    def _create_snapshot(self, root: str) -> str:
        root = os.path.abspath(root)
        root_name = os.path.basename(root)
        snap_name = f"{root_name}{ts_code()}"
        snap_dir = os.path.join(root, snap_name)
        deny = {"venv", ".venv", "LOG", snap_name}

        def ignore(dirpath, names):
            ignored = set()
            for n in names:
                if n in deny:
                    ignored.add(n)
                elif is_versing_snapshot_dir(n, root_name):
                    ignored.add(n)
            return ignored

        shutil.copytree(root, snap_dir, ignore=ignore, dirs_exist_ok=True)
        try:
            self.log.event("versing.snapshot.created", {"snap_dir": snap_dir})
        except Exception:
            pass
        return snap_dir

    def _save_out_files(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        out_dir = self.cfg.out_dir
        ensure_dir(out_dir)

        if self.cfg.versing and files:
            self._set(80, 0, "VERSING: snapshot before write...")
            self._create_snapshot(out_dir)

        saved: List[Dict[str, Any]] = []
        for i, f in enumerate(files):
            self._check_stop()
            rel = f["path"]
            content = f.get("content", "")
            dst = os.path.join(out_dir, rel.replace("/", os.sep))
            ensure_dir(os.path.dirname(dst))
            before_size = os.path.getsize(dst) if os.path.exists(dst) else None
            before = sha256_file(dst, max_bytes=2 * 1024 * 1024) if os.path.exists(dst) else None
            with open(dst, "w", encoding="utf-8", newline="\n") as fp:
                fp.write(content)
            after_size = os.path.getsize(dst)
            after = sha256_file(dst, max_bytes=2 * 1024 * 1024)
            self.log.record_fs_change("write", src=rel, dst=dst, before=before, after=after, before_size=before_size, after_size=after_size)
            saved.append({"path": rel, "dst": dst, "bytes": after_size})
            self.subprogress.emit(int((i + 1) * 100 / max(1, len(files))))
        self.log.save_json("manifests", "out_saved_map", {"saved": saved, "out_dir": out_dir})
        return {"saved": saved}

    def _write_missing_files_md(self, out_dir: str, missing_paths: List[str]) -> None:
        if not missing_paths:
            return
        ensure_dir(out_dir)
        md_path = os.path.join(out_dir, "MISSINGFILES.md")
        try:
            lines = [
                "# Missing image files",
                "",
                "Tyto soubory (PNG/JPG) nebyly vygenerovány automaticky. Doplň je ručně nebo dodáš parametry a znovu spusť GENERATE.",
                "",
                "Seznam chybějících souborů:",
            ]
            for p in missing_paths:
                lines.append(f"- `{p}`")
            lines += [
                "",
                "Jak doplnit:",
                "1) Vytvoř soubory ručně na uvedených relativních cestách v OUT.",
                "2) Nebo přilož vstupní obrázky do Files API a uveď je v zadání.",
                "3) Pokud chceš, aby je model vygeneroval později, doplň pro každý soubor:",
                "   - `path`: relativní cesta (viz výše)",
                "   - `format`: png|jpg",
                "   - `width` x `height` (px) nebo aspoň poměr stran",
                "   - `description`: co má obrázek obsahovat",
                "   - `style` (volitelné): např. flat, photo, line-art",
                "",
                "Ulož tyto parametry do zadání nebo přímo do tohoto souboru a spusť ReRun.",
            ]
            with open(md_path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            try:
                self.log.event("missing.images", {"count": len(missing_paths), "paths": missing_paths, "note": md_path})
            except Exception:
                pass
        except Exception as e:
            self._log_debug(f"MISSINGFILES.md write failed: {e}")

    # ---------- receipts ----------
    def _usage_from_resp(self, resp: Dict[str, Any]) -> Tuple[int, int, Dict[str, Any]]:
        usage = resp.get("usage") or {}
        if not isinstance(usage, dict):
            return 0, 0, {}
        inp = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        return inp, out, usage

    def _record_receipt(
        self,
        resp: Dict[str, Any],
        mode: str,
        flow_type: str,
        response_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        is_batch: bool = False,
        stage: str = "",
    ):
        inp, out, usage = self._usage_from_resp(resp or {})
        model_for_cost = self._generate_model(stage) if stage else self.cfg.model_default
        row = self.price_table.get(model_for_cost) or PriceTable.builtin_fallback().get(model_for_cost) or PriceTable.builtin_fallback().get("gpt-4o-mini")
        verified = bool(self.price_table.verified and row is not None)
        total, tool_cost, storage_cost = compute_cost(row, inp, out, is_batch=is_batch, use_file_search=self._used_file_search)
        r = Receipt(
            run_id=self.log.run_id,
            created_at=time.time(),
            project=self.cfg.project,
            model=model_for_cost,
            mode=mode,
            flow_type=flow_type,
            stage=stage,
            response_id=response_id,
            batch_id=batch_id,
            input_tokens=inp,
            output_tokens=out,
            tool_cost=float(tool_cost),
            storage_cost=float(storage_cost),
            total_cost=float(total),
            cost=float(total),
            pricing_verified=verified,
            notes=(self.cfg.prompt or "")[:4000],
            log_paths={"run_dir": self.log.paths.run_dir},
            usage=usage,
        )
        self.db.insert(r)
        self._has_receipt = True
        self._total_input_tokens += inp
        self._total_output_tokens += out

    def _ensure_receipt_on_failure(self, reason: str, flow_type: str):
        if self._has_receipt:
            return
        row = self.price_table.get(self.cfg.model_default) or PriceTable.builtin_fallback().get(self.cfg.model_default) or PriceTable.builtin_fallback().get("gpt-4o-mini")
        verified = bool(self.price_table.verified and row is not None)
        total, tool_cost, storage_cost = compute_cost(row, self._total_input_tokens, self._total_output_tokens, is_batch=self.cfg.send_as_c, use_file_search=self._used_file_search)
        r = Receipt(
            run_id=self.log.run_id,
            created_at=time.time(),
            project=self.cfg.project,
            model=self.cfg.model_default,
            mode=self.cfg.mode,
            flow_type=flow_type,
            stage="FAILED",
            response_id=None,
            batch_id=None,
            input_tokens=self._total_input_tokens,
            output_tokens=self._total_output_tokens,
            tool_cost=float(tool_cost),
            storage_cost=float(storage_cost),
            total_cost=float(total),
            cost=float(total),
            pricing_verified=verified,
            notes=f"Fallback receipt ({reason})",
            log_paths={"run_dir": self.log.paths.run_dir},
            usage={"reason": reason},
        )
        self.db.insert(r)
        self._has_receipt = True

    # ---------- A: GENERATE ----------
    def _run_a_generate(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        # Resume path: skip A1/A2 if structure is already known (ReRun)
        plan = {}
        resp2 = None
        a1_model = self._generate_model("A1")
        a2_model = self._generate_model("A2")
        files: List[Dict[str, Any]] = []
        missing_images: List[str] = []
        auto_skip_image_exts = {".png", ".jpg", ".jpeg"}
        handoff_mode = "prev_chain"
        if self.cfg.resume_files:
            self._set(10, 0, "ReRun: using existing A2 structure, skipping A1/A2")
            struct = {"contract": "A2_STRUCTURE", "files": self.cfg.resume_files}
            resp2_id = self.cfg.resume_prev_id or self.cfg.response_id or None
            try:
                # persist resume metadata for future ReRun chains
                self.log.save_json(
                    "manifests",
                    f"resume_structure_{ts_code()}",
                    {"resume_files": self.cfg.resume_files, "resume_prev_id": resp2_id},
                )
            except Exception:
                pass

            files_raw = struct.get("files", []) or []
            for f in files_raw:
                self._check_stop()
                path = f.get("path")
                if not isinstance(path, str) or not path:
                    continue
                ext = os.path.splitext(path)[1].lower()
                if ext in auto_skip_image_exts:
                    self._log_debug(f"A3: skipping generated image extension {ext} ({path})")
                    missing_images.append(path)
                    continue
                if ext in (self.cfg.skip_exts or []):
                    self._log_debug(f"A3: skipping due to extension {ext} ({path})")
                    continue
                if path in (self.cfg.skip_paths or []):
                    self._log_debug(f"A3: skipping already completed {path}")
                    continue
                files.append(f)

            try:
                artifacts = self._create_a3_handoff_artifacts(files)
                handoff_mode = self._choose_a3_generation_mode(artifacts)
            except Exception as e:
                self._log_debug(f"A3 handoff artifacts unavailable: {e}")
                handoff_mode = "prev_chain"
        else:
            self._set(10, 0, "A1: PLAN request...")
            a1_schema = (
                '{"contract":"A1_PLAN","project":{"name":"string","one_liner":"string","target_os":"string","language":"string","runtime":"string"},'
                '"assumptions":["string"],"requirements":{"functional":["string"],"non_functional":["string"],"constraints":["string"]},'
                '"architecture":{"modules":[{"name":"string","responsibility":"string"}],"data_flow":["string"],"error_handling":["string"],"security_notes":["string"]},'
                '"build_run":{"prerequisites":["string"],"commands":["string"],"verification":["string"]},"deliverable_policy":{"max_lines_per_chunk":500}}'
            )
            instructions = (
                "Jsi senior software architekt a implementátor. "
                "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
                f"KONTRAKT A1_PLAN: {a1_schema}"
            )

            # If long prompt was ingested (A0), do not resend; reference it.
            a1_text = (self.cfg.prompt or "") if len(self.cfg.prompt or "") <= 150_000 else "Použij ingested Zadání (A0) a přiložené soubory, a vrať A1 plan dle kontraktu."
            note = self._in_dir_fallback_note()
            if note:
                a1_text = f"{a1_text}\n\n{note}"
            a1_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
            a1_input_files, a1_input_images = self._build_input_attachments(client, self._input_file_ids())
            a1_text = self._append_io_reference(a1_text, a1_ref_files)
            a1_text = self._with_diag_text(a1_text)
            instructions = self._append_io_reference_instructions(instructions, a1_ref_files)
            payload = self._payload_base(
                stage="A1",
                instructions=instructions,
                input_parts=self._input_parts(a1_text, a1_input_files, a1_input_images),
                prev_id=base_prev_id,
            )
            if self._fs_tools:
                payload["tools"] = self._fs_tools
                self._used_file_search = True
            self._log_request_attachments("A1", a1_ref_files, a1_input_files, a1_input_images, self._vector_store_ids, self._fs_tools)
            self._log_api_action(
                "A1",
                "prepare",
                {
                    "prompt_len": len(a1_text),
                    "files": len(self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)),
                },
            )
            self.log.save_json(
                "requests",
                f"A1_request_{ts_code()}",
                {
                    "payload": payload,
                    "ui_state": self._ui_state_for_log(),
                    **self._request_model_fields(),
                    "attachments": self._attachments_snapshot("A1", a1_ref_files, a1_input_files, a1_input_images, self._vector_store_ids, self._fs_tools),
                },
            )
            self._log_api_action("A1", "send", {"contract": "A1_PLAN", "stage": "PLAN", "model": a1_model})
            resp1 = with_retry(lambda: client.create_response(payload), self.settings.retry, self.breaker)
            self.log.save_json("responses", f"A1_response_{resp1.get('id','NOID')}_{ts_code()}", resp1)
            self._log_api_action("A1", "receive", {"response_id": resp1.get("id"), "status": resp1.get("status"), "contract": "A1_PLAN"})

            resp1_id = str(resp1.get("id") or "")
            plan = parse_json_strict(extract_text_from_response(resp1))
            if plan.get("contract") != "A1_PLAN":
                raise ContractError("A1_PLAN contract mismatch")

            self._set(20, 0, "A2: STRUCTURE request...")
            a2_schema = '{"contract":"A2_STRUCTURE","root":"string","files":[{"path":"string","purpose":"string","language":"string","generated_in_phase":"A3"}]}'
            instructions2 = (
                "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
                f"KONTRAKT A2_STRUCTURE: {a2_schema}"
            )
            a2_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
            a2_input_files, a2_input_images = self._build_input_attachments(client, self._input_file_ids())
            instructions2 = self._append_io_reference_instructions(instructions2, a2_ref_files)
            a2_text = self._append_io_reference("Vygeneruj strukturu souborů podle A1 plánu.", a2_ref_files)
            a2_text = self._with_diag_text(a2_text)
            payload2 = self._payload_base(
                stage="A2",
                instructions=instructions2,
                input_parts=self._input_parts(
                    a2_text,
                    a2_input_files,
                    a2_input_images,
                ),
                prev_id=resp1_id,
            )
            if self._fs_tools:
                payload2["tools"] = self._fs_tools
                self._used_file_search = True
            self._log_request_attachments("A2", a2_ref_files, a2_input_files, a2_input_images, self._vector_store_ids, self._fs_tools)
            self._log_api_action(
                "A2",
                "prepare",
                {
                    "files": len(self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)),
                },
            )
            self.log.save_json(
                "requests",
                f"A2_request_{ts_code()}",
                {
                    "payload": payload2,
                    "ui_state": self._ui_state_for_log(),
                    **self._request_model_fields(),
                    "attachments": self._attachments_snapshot("A2", a2_ref_files, a2_input_files, a2_input_images, self._vector_store_ids, self._fs_tools),
                },
            )
            self._log_api_action("A2", "send", {"contract": "A2_STRUCTURE", "stage": "STRUCTURE", "model": a2_model})
            resp2 = with_retry(lambda: client.create_response(payload2), self.settings.retry, self.breaker)
            self.log.save_json("responses", f"A2_response_{resp2.get('id','NOID')}_{ts_code()}", resp2)
            self._log_api_action("A2", "receive", {"response_id": resp2.get("id"), "status": resp2.get("status"), "contract": "A2_STRUCTURE"})

            resp2_id = str(resp2.get("id") or "")
            struct = parse_json_strict(extract_text_from_response(resp2))
            if struct.get("contract") != "A2_STRUCTURE":
                raise ContractError("A2_STRUCTURE contract mismatch")
            try:
                # persist structure for any future ReRun (even if this run is interrupted later)
                self.log.save_json(
                    "manifests",
                    f"resume_structure_{ts_code()}",
                    {"resume_files": struct.get("files", []) or [], "resume_prev_id": resp2_id},
                )
            except Exception:
                pass

            files_raw = struct.get("files", []) or []
            for f in files_raw:
                self._check_stop()
                path = f.get("path")
                if not isinstance(path, str) or not path:
                    continue
                ext = os.path.splitext(path)[1].lower()
                if ext in auto_skip_image_exts:
                    self._log_debug(f"A3: skipping generated image extension {ext} ({path})")
                    missing_images.append(path)
                    continue
                if ext in (self.cfg.skip_exts or []):
                    self._log_debug(f"A3: skipping due to extension {ext} ({path})")
                    continue
                if path in (self.cfg.skip_paths or []):
                    self._log_debug(f"A3: skipping already completed {path}")
                    continue
                files.append(f)

            try:
                artifacts = self._create_a3_handoff_artifacts(files)
                handoff_mode = self._choose_a3_generation_mode(artifacts)
            except Exception as e:
                self._log_debug(f"A3 handoff artifacts unavailable: {e}")
                handoff_mode = "prev_chain"

        total_files = len(files)
        chain_prev_id = str(resp2_id or "")
        out_files: List[Dict[str, Any]] = []
        if handoff_mode == "handoff":
            artifacts = self._load_a3_handoff_artifacts()
            if self._choose_a3_generation_mode(artifacts) == "handoff":
                self._log_debug("A3 using structure handoff")
                out_files, errors = self._generate_full_files_parallel(
                    client,
                    files,
                    diag_file_ids,
                    artifacts.get("context") or {},
                    artifacts.get("contracts") or {},
                    artifacts.get("structure") or {},
                )
                for err in errors:
                    self._log_debug(f"A3 error: {err}")
            else:
                handoff_mode = "prev_chain"

        if handoff_mode != "handoff":
            self._log_debug("A3 using previous_response chain")
            for idx, f in enumerate(files, start=1):
                self._check_stop()
                path = f.get("path")
                # file-level progress (N of total)
                self.subprogress.emit(int(idx * 100 / max(1, total_files)))
                self._set(30 + int(45 * (idx - 1) / max(1, len(files))), 0, f"A3: FILE {path} ({idx}/{total_files})")
                content, last_resp_id = self._gen_file_chunks(
                    client,
                    prev_id=chain_prev_id,
                    contract="A3_FILE",
                    path=path,
                    action=None,
                    diag_file_ids=diag_file_ids,
                    tools=self._fs_tools,
                )
                if last_resp_id:
                    chain_prev_id = last_resp_id
                out_files.append({"path": path, "content": content, "purpose": f.get("purpose", "")})

        saved_map = self._save_out_files(out_files)
        if missing_images:
            self._write_missing_files_md(self.cfg.out_dir, missing_images)
        if resp2 is not None:
            self._record_receipt(resp2, mode="GENERATE", flow_type="A", response_id=resp2_id, stage="A2")
        return {"mode": "GENERATE", "plan": plan, "structure": struct, "saved": saved_map, "response_id": resp2_id}

    # ---------- B: MODIFY ----------
    def _run_b_modify(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        self._set(8, 0, "IN mirror: scan + manifest + upload...")
        root = self.cfg.in_dir
        root_name = os.path.basename(os.path.abspath(root))

        items = scan_tree(
            root,
            root_name,
            deny_dirs=["venv", ".venv", "LOG"],
            deny_exts=self.settings.security.deny_extensions_in,
            allow_exts=self.settings.security.allow_extensions_in,
            deny_globs=self.settings.security.deny_globs_in,
            allow_globs=self.settings.security.allow_globs_in,
        )
        manifest = build_manifest(root, items, extra={"project": self.cfg.project})
        manifest_path = os.path.join(self.log.paths.manifests_dir, f"mirror_manifest_{ts_code()}.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        mf_up = with_retry(lambda: client.upload_file(manifest_path, purpose="user_data"), self.settings.retry, self.breaker)
        manifest_file_id = mf_up["id"]
        self._remember_file_name(manifest_file_id, os.path.basename(manifest_path))
        self._log_debug(f"Mirror manifest uploaded: {manifest_file_id}")

        uploaded: List[Tuple[str, str]] = []
        up_items = [it for it in items if it.uploadable]
        for i, it in enumerate(up_items):
            self._check_stop()
            self.subprogress.emit(int((i + 1) * 100 / max(1, len(up_items))))
            self._log_debug(f"Upload mirror file: {it.rel_path}")
            up = with_retry(lambda p=it.abs_path: client.upload_file(p, purpose="user_data"), self.settings.retry, self.breaker)
            uploaded.append((it.rel_path, up["id"]))
            self._remember_file_name(up["id"], os.path.basename(it.abs_path))
            try:
                self.log.event("upload.mirror", {"path": it.rel_path, "abs": it.abs_path, "file_id": up["id"], "bytes": it.size})
            except Exception:
                pass

        self.log.save_json("manifests", "mirror_manifest", {"manifest_file_id": manifest_file_id, "uploaded": uploaded, "manifest": manifest})

        tools: Optional[List[Dict[str, Any]]] = None
        vs_id: Optional[str] = None
        vs_ids: List[str] = list(self._vector_store_ids or [])
        caps_b = self._caps_for_step("B1")
        supports_fs = bool(caps_b.get("supports_file_search", False)) and bool(self.cfg.use_file_search)

        if supports_fs:
            try:
                self._set(18, 0, "Vector store: create + attach (file_search)...")
                vs = with_retry(lambda: client.create_vector_store(f"{(self.cfg.project or root_name)}{ts_code()}"), self.settings.retry, self.breaker)
                vs_id = vs.get("id")
                if vs_id:
                    vs_file_ids: List[str] = []
                    for rel, fid in uploaded[:2000]:
                        self._check_stop()
                        vs_file = with_retry(
                            lambda v=vs_id, f=fid, r=rel: client.add_file_to_vector_store(v, f, attributes={"source_path": os.path.join(root, rel)}),
                            self.settings.retry,
                            self.breaker,
                        )
                        try:
                            vs_file_id = str(vs_file.get("id") or "")
                            if vs_file_id:
                                vs_file_ids.append(vs_file_id)
                        except Exception:
                            pass
                    mf_vs_file = with_retry(lambda: client.add_file_to_vector_store(vs_id, manifest_file_id, attributes={"source": "mirror_manifest"}), self.settings.retry, self.breaker)
                    try:
                        mf_vs_id = str(mf_vs_file.get("id") or "")
                        if mf_vs_id:
                            vs_file_ids.append(mf_vs_id)
                    except Exception:
                        pass
                    if vs_file_ids:
                        self._wait_vector_store_files(client, vs_id, vs_file_ids)
                    vs_ids.append(vs_id)
                    self._vector_store_ids.append(vs_id)
            except Exception as e:
                # fallback: still allow pre-attached vector stores if any exist
                supports_fs = bool(vs_ids)
                tools = None
                vs_id = None
                try:
                    self.log.exception("vector_store", e)
                except Exception:
                    pass

        if supports_fs and vs_ids:
            # dedupe preserving order
            seen = set()
            uniq_ids: List[str] = []
            for vid in vs_ids:
                if vid and vid not in seen:
                    uniq_ids.append(vid)
                    seen.add(vid)
            tools = [{"type": "file_search", "vector_store_ids": uniq_ids}]
            if tools:
                self._used_file_search = True
                self._fs_tools = tools

        self._set(24, 0, "B1: PLAN (modify)...")
        b1_schema = (
            '{"contract":"B1_PLAN","diagnosis":{"summary":"string","evidence":[{"path":"string","reason":"string"}],"likely_root_causes":["string"]},'
            '"change_plan":{"goals":["string"],"files_to_modify":[{"path":"string","intent":"string"}],"files_to_add":[{"path":"string","intent":"string"}],"verification_steps":["string"]},'
            '"missing_inputs":["string"]}'
        )
        instructions1 = (
            "Jsi senior maintenance inženýr. Pokud je dostupné file_search, použij jej. "
            "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
            f"KONTRAKT B1_PLAN: {b1_schema}"
        )
        b1_text = (self.cfg.prompt or "") if len(self.cfg.prompt or "") <= 150_000 else "Použij ingested Zadání (A0) + přiložené soubory. Vrať B1 plan dle kontraktu."
        note = self._in_dir_fallback_note()
        if note:
            b1_text = f"{b1_text}\n\n{note}"
        b1_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids + [manifest_file_id] + [fid for _, fid in uploaded])
        b1_input_files, b1_input_images = self._build_input_attachments(
            client, self._input_file_ids() + [manifest_file_id] + [fid for _, fid in uploaded]
        )
        b1_text = self._append_io_reference(b1_text, b1_ref_files)
        b1_text = self._with_diag_text(b1_text)
        instructions1 = self._append_io_reference_instructions(instructions1, b1_ref_files)
        payload1 = self._payload_base(
            stage="B1",
            instructions=instructions1,
            input_parts=self._input_parts(
                b1_text,
                b1_input_files,
                b1_input_images,
            ),
            prev_id=base_prev_id,
        )
        if supports_fs and tools:
            payload1["tools"] = tools
            self._used_file_search = True
        self._log_request_attachments("B1", b1_ref_files, b1_input_files, b1_input_images, vs_ids, tools)

        self.log.save_json(
            "requests",
            f"B1_request_{ts_code()}",
            {
                "payload": payload1,
                "ui_state": self._ui_state_for_log(),
                **self._request_model_fields(),
                "supports_file_search": supports_fs,
                "vector_store_ids": vs_ids,
                "attachments": self._attachments_snapshot("B1", b1_ref_files, b1_input_files, b1_input_images, vs_ids, tools),
            },
        )
        self._log_api_action("B1", "send", {"contract": "B1_PLAN"})
        resp1 = with_retry(lambda: client.create_response(payload1), self.settings.retry, self.breaker)
        self.log.save_json("responses", f"B1_response_{resp1.get('id','NOID')}_{ts_code()}", resp1)
        self._log_api_action("B1", "receive", {"response_id": resp1.get("id"), "status": resp1.get("status"), "contract": "B1_PLAN"})

        resp1_id = str(resp1.get("id") or "")
        plan = parse_json_strict(extract_text_from_response(resp1))
        if plan.get("contract") != "B1_PLAN":
            raise ContractError("B1_PLAN contract mismatch")

        self._set(36, 0, "B2: STRUCTURE (touched files)...")
        b2_schema = '{"contract":"B2_STRUCTURE","touched_files":[{"path":"string","action":"modify|add","intent":"string"}],"invariants":["string"]}'
        instructions2 = (
            "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
            f"KONTRAKT B2_STRUCTURE: {b2_schema}"
        )
        b2_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids + [manifest_file_id] + [fid for _, fid in uploaded])
        b2_input_files, b2_input_images = self._build_input_attachments(
            client, self._input_file_ids() + [manifest_file_id] + [fid for _, fid in uploaded]
        )
        instructions2 = self._append_io_reference_instructions(instructions2, b2_ref_files)
        b2_text = self._append_io_reference("Vrať seznam touched_files pro implementaci B3.", b2_ref_files)
        b2_text = self._with_diag_text(b2_text)
        payload2 = self._payload_base(
            stage="B2",
            instructions=instructions2,
            input_parts=self._input_parts(
                b2_text,
                b2_input_files,
                b2_input_images,
            ),
            prev_id=resp1_id,
        )
        if supports_fs and tools:
            payload2["tools"] = tools
            self._used_file_search = True
        self._log_request_attachments("B2", b2_ref_files, b2_input_files, b2_input_images, vs_ids, tools)

        self.log.save_json(
            "requests",
            f"B2_request_{ts_code()}",
            {
                "payload": payload2,
                "ui_state": self._ui_state_for_log(),
                **self._request_model_fields(),
                "attachments": self._attachments_snapshot("B2", b2_ref_files, b2_input_files, b2_input_images, vs_ids, tools),
            },
        )
        self._log_api_action("B2", "send", {"contract": "B2_STRUCTURE"})
        resp2 = with_retry(lambda: client.create_response(payload2), self.settings.retry, self.breaker)
        self.log.save_json("responses", f"B2_response_{resp2.get('id','NOID')}_{ts_code()}", resp2)
        self._log_api_action("B2", "receive", {"response_id": resp2.get("id"), "status": resp2.get("status"), "contract": "B2_STRUCTURE"})

        resp2_id = str(resp2.get("id") or "")
        struct = parse_json_strict(extract_text_from_response(resp2))
        if struct.get("contract") != "B2_STRUCTURE":
            raise ContractError("B2_STRUCTURE contract mismatch")

        touched_raw = struct.get("touched_files", []) or []
        touched = []
        for tf in touched_raw:
            path = tf.get("path", "")
            if not path:
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in (self.cfg.skip_exts or []):
                self._log_debug(f"B3: skipping due to extension {ext} ({path})")
                continue
            if path in (self.cfg.skip_paths or []):
                self._log_debug(f"B3: skipping already completed {path}")
                continue
            touched.append(tf)

        total_files = len(touched)
        chain_prev_id = str(resp2_id or "")
        out_files: List[Dict[str, Any]] = []
        for i, tf in enumerate(touched, start=1):
            self._check_stop()
            path = tf.get("path", "")
            action = tf.get("action", "modify")
            # file-level progress (N of total)
            self.subprogress.emit(int(i * 100 / max(1, total_files)))
            self._set(50 + int(35 * (i - 1) / max(1, len(touched))), 0, f"B3: {action} {path} ({i}/{total_files})")
            content, last_resp_id = self._gen_file_chunks(
                client,
                prev_id=chain_prev_id,
                contract="B3_FILE",
                path=path,
                action=action,
                diag_file_ids=diag_file_ids,
                tools=tools if supports_fs else None,
            )
            if last_resp_id:
                chain_prev_id = last_resp_id
            out_files.append({"path": path, "content": content})

        saved_map = self._save_out_files(out_files)
        self._record_receipt(resp2, mode="MODIFY", flow_type="B", response_id=resp2_id, stage="B2")
        return {"mode": "MODIFY", "plan": plan, "structure": struct, "saved": saved_map, "response_id": resp2_id, "vector_store_id": vs_id, "supports_file_search": supports_fs}

    # ---------- QA ----------
    def _run_qa(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        self._set(10, 0, "QA: request...")
        note = self._in_dir_fallback_note()
        input_text = self.cfg.prompt or ""
        if note:
            input_text = f"{input_text}\n\n{note}"
        # Redundant instruction: QA must return plain text only, no files or markdown.
        qa_note = "Pozn.: Vrat pouze cisty text (bez markdownu) a neposilej zadne soubory."
        if qa_note not in input_text:
            input_text = f"{input_text}\n\n{qa_note}"
        ref_file_ids = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        input_file_ids, input_image_ids = self._build_input_attachments(client, self._input_file_ids())
        input_text = self._append_io_reference(input_text, ref_file_ids)
        input_text = self._with_diag_text(input_text)
        input_parts = self._input_parts(input_text, input_file_ids, input_image_ids)
        payload = self._payload_base(
            stage="QA",
            instructions=self._append_io_reference_instructions(
                "Jsi QA asistent. Vrat pouze cisty text bez markdownu, bez souboru.",
                ref_file_ids,
            ),
            input_parts=input_parts,
            prev_id=base_prev_id,
        )
        if self._fs_tools:
            payload["tools"] = self._fs_tools
            self._used_file_search = True
        self._log_request_attachments("QA", ref_file_ids, input_file_ids, input_image_ids, self._vector_store_ids, self._fs_tools)
        self._log_api_action(
            "QA",
            "prepare",
            {
                "prompt_len": len(self.cfg.prompt or ""),
                "files": len(ref_file_ids),
            },
        )
        self.log.save_json(
            "requests",
            f"QA_request_{ts_code()}",
            {
                "payload": payload,
                "ui_state": self._ui_state_for_log(),
                **self._request_model_fields(),
                "attachments": self._attachments_snapshot("QA", ref_file_ids, input_file_ids, input_image_ids, self._vector_store_ids, self._fs_tools),
            },
        )
        self._log_api_action("QA", "send", {"description": "QA request"})
        resp = with_retry(lambda: client.create_response(payload), self.settings.retry, self.breaker)
        self.log.save_json("responses", f"QA_response_{resp.get('id','NOID')}_{ts_code()}", resp)
        self._log_api_action("QA", "receive", {"response_id": resp.get("id"), "status": resp.get("status")})
        self._record_receipt(resp, mode="QA", flow_type="QA", response_id=str(resp.get("id") or ""), stage="QA")
        return {"mode": "QA", "response_id": str(resp.get("id") or ""), "text": extract_text_from_response(resp)}

    # ---------- QFile ----------
    def _run_qfile(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        self._set(10, 0, "QFILE: request...")
        self._check_stop()
        prompt = (self.cfg.prompt or "").strip()
        if not prompt:
            raise RuntimeError("QFILE: Zadání je prázdné.")
        note = self._in_dir_fallback_note()
        if note:
            prompt = f"{prompt}\n\n{note}"
        qfile_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        qfile_input_files, qfile_input_images = self._build_input_attachments(client, self._input_file_ids())
        prompt = self._append_io_reference(prompt, qfile_ref_files)
        prompt = self._with_diag_text(prompt)

        schema = '{"contract":"A3_FILE","path":"string","chunking":{"max_lines":500,"chunk_index":0,"chunk_count":0,"has_more":false,"next_chunk_index":null},"content":"string"}'
        instructions = (
            "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
            "KRITICKÉ: content je vždy kompletní výsledné znění souboru (ne diff/patch). "
            f"CHUNK: max 500 řádků. KONTRAKT: {schema}"
        )
        gen_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        instructions = self._append_io_reference_instructions(instructions, gen_ref_files)
        instructions = self._append_io_reference_instructions(instructions, qfile_ref_files)
        input_text = (
            "Vrať kompletní obsah jednoho souboru dle zadání níže. "
            "CHUNK_INDEX=0, chunk_count=1, chunking.has_more=false (QFILE je jednorázový request). "
            "Použij cestu/path popsanou v zadání (žádný manifest). "
            f"Zadání:\n{prompt}"
        )
        payload = self._payload_base(
            stage="QFILE",
            instructions=instructions,
            input_parts=self._input_parts(input_text, qfile_input_files, qfile_input_images),
            prev_id=base_prev_id,
        )

        caps_q = self._caps_for_step("QFILE")
        if caps_q.get("supports_temperature", True):
            payload["temperature"] = 0.0
        if self._fs_tools:
            payload["tools"] = self._fs_tools
            self._used_file_search = True
        self._log_request_attachments("QFILE", qfile_ref_files, qfile_input_files, qfile_input_images, self._vector_store_ids, self._fs_tools)

        self.log.save_json(
            "requests",
            f"QFILE_request_{ts_code()}",
            {
                "payload": payload,
                "ui_state": self._ui_state_for_log(),
                **self._request_model_fields(),
                "attachments": self._attachments_snapshot("QFILE", qfile_ref_files, qfile_input_files, qfile_input_images, self._vector_store_ids, self._fs_tools),
            },
        )
        self._log_api_action(
            "QFILE",
            "send",
            {
                "prev_id": base_prev_id,
                "files": len(self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)),
            },
        )
        resp = with_retry(lambda: client.create_response(payload), self.settings.retry, self.breaker)
        self.log.save_json("responses", f"QFILE_response_{resp.get('id','NOID')}_{ts_code()}", resp)
        self._log_api_action("QFILE", "receive", {"response_id": resp.get("id"), "status": resp.get("status")})

        raw_text = extract_text_from_response(resp)
        parsed = parse_json_strict(raw_text)
        if parsed.get("contract") != "A3_FILE":
            raise ContractError("QFILE: očekáván kontrakt A3_FILE")

        chunk = parsed.get("chunking") or {}
        if chunk.get("has_more"):
            raise ContractError("QFILE: chunking.has_more musí být false (jediný chunk).")

        path = parsed.get("path")
        if not isinstance(path, str) or not path:
            raise ContractError("QFILE: chybí path v odpovědi.")

        out_files = [{"path": path, "content": parsed.get("content", ""), "purpose": "QFILE"}]
        self._set(70, 0, f"QFILE: ukládám {path}...")
        saved_map = self._save_out_files(out_files)
        self._record_receipt(resp, mode="QFILE", flow_type="QFILE", response_id=str(resp.get("id") or ""), stage="QFILE")
        return {"mode": "QFILE", "response_id": str(resp.get("id") or ""), "saved": saved_map, "contract": parsed, "text": raw_text}

    # ---------- C: Batch ----------
    def _run_c_batch(self, client: OpenAIClient, diag_file_ids: List[str], base_prev_id: Optional[str]) -> Dict[str, Any]:
        self._set(10, 0, "C: building batch JSONL...")
        c_schema = (
            '{"contract":"C_FILES_ALL","project":{"name":"string","target_os":"Windows 10/11","runtime":"string","language":"string"},'
            '"root":"string","files":[{"path":"relative/path/file.ext","purpose":"string","content":"string"}],'
            '"build_run":{"prerequisites":["string"],"commands":["string"],"verification":["string"]},"notes":["string"]}'
        )
        instructions = (
            "Jsi senior programátor. OUTPUT: VRAŤ POUZE validní JSON dokument dle KONTRAKTU C_FILES_ALL. "
            "ŽÁDNÝ markdown ani další text. "
            f"KONTRAKT C_FILES_ALL: {c_schema}"
        )
        note = self._in_dir_fallback_note()
        if note:
            instructions = f"{instructions} {note}"

        prompt_text = self.cfg.prompt or ""
        if note:
            prompt_text = f"{prompt_text}\n\n{note}"
        c_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        c_input_files, c_input_images = self._build_input_attachments(client, self._input_file_ids())
        prompt_text = self._append_io_reference(prompt_text, c_ref_files)
        prompt_text = self._with_diag_text(prompt_text)
        instructions = self._append_io_reference_instructions(instructions, c_ref_files)

        body: Dict[str, Any] = {
            "model": self._generate_model("C"),
            "instructions": instructions,
            "input": self._input_parts(prompt_text, c_input_files, c_input_images),
        }
        caps_c = self._caps_for_step("C")
        if caps_c.get("supports_temperature", True):
            body["temperature"] = float(self.cfg.temperature)
        self._log_request_attachments("C", c_ref_files, c_input_files, c_input_images, self._vector_store_ids, None)
        self._log_api_action(
            "C",
            "prepare",
            {
                "prompt_len": len(prompt_text or ""),
                "files": len(self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)),
            },
        )
        # BATCH does not use previous_response_id (even if user provided one).
        req_line = {
            "custom_id": f"{self.log.run_id}_C1",
            "method": "POST",
            "url": "/v1/responses",
            "body": body,
            "attachments": self._attachments_snapshot("C", c_ref_files, c_input_files, c_input_images, self._vector_store_ids, None),
        }

        jsonl_path = os.path.join(self.log.paths.requests_dir, f"C_batch_{ts_code()}.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(req_line, ensure_ascii=False) + "\n")
        try:
            size = os.path.getsize(jsonl_path)
        except Exception:
            size = None
        self._log_api_action("C", "jsonl", {"path": jsonl_path, "size": size})

        self._set(20, 0, "C: upload JSONL...")
        self._log_debug("C: uploading batch JSONL")
        up = with_retry(lambda: client.upload_file(jsonl_path, purpose="batch"), self.settings.retry, self.breaker)
        input_file_id = up["id"]
        self._log_api_action("C", "upload", {"input_file_id": input_file_id, "retry": self.settings.retry.max_attempts if hasattr(self.settings.retry, 'max_attempts') else None})

        self._set(25, 0, "C: create batch...")
        self._log_debug("C: creating batch")
        batch = with_retry(lambda: client.create_batch(input_file_id=input_file_id, endpoint="/v1/responses"), self.settings.retry, self.breaker)
        batch_id = str(batch.get("id") or "")
        self.log.update_state({"batch_id": batch_id})
        self.log.save_json("responses", f"C_batch_created_{batch_id}_{ts_code()}", batch)
        self._log_api_action("C", "create", {"batch_id": batch_id, "status": batch.get("status")})

        # For batch we stop after creation and hand off monitoring to Batch tab.
        self._set(100, 0, f"C: batch created ({batch_id})")
        self.log.event("batch.created", {"batch_id": batch_id, "input_file_id": input_file_id})
        return {"mode": "C", "batch_id": batch_id, "status": batch.get("status"), "input_file_id": input_file_id}

    # ---------- file generation (A3/B3) ----------
    def _gen_file_chunks(
        self,
        client: OpenAIClient,
        prev_id: str,
        contract: str,
        path: str,
        action: Optional[str],
        diag_file_ids: List[str],
        tools: Optional[List[Dict[str, Any]]] = None,
        extra_prompt: Optional[str] = None,
    ) -> Tuple[str, str]:
        if contract == "A3_FILE":
            schema = '{"contract":"A3_FILE","path":"string","chunking":{"max_lines":500,"chunk_index":0,"chunk_count":0,"has_more":false,"next_chunk_index":null},"content":"string"}'
        else:
            schema = '{"contract":"B3_FILE","path":"string","action":"modify|add","chunking":{"max_lines":500,"chunk_index":0,"chunk_count":0,"has_more":false,"next_chunk_index":null},"content":"string","notes":["string"]}'
        instructions = (
            "OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. "
            "KRITICKÉ: content je vždy kompletní výsledné znění souboru (ne diff/patch). "
            f"CHUNK: max 500 řádků. KONTRAKT: {schema}"
        )
        gen_ref_files = self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)
        gen_input_files, gen_input_images = self._build_input_attachments(client, self._input_file_ids())
        instructions = self._append_io_reference_instructions(instructions, gen_ref_files)

        chunk_index = 0
        parts: List[str] = []
        latest_response_id = str(prev_id or "")
        stage = "A3" if contract == "A3_FILE" else "B3"
        step_caps = self._caps_for_step(stage)
        while True:
            self._check_stop()
            if contract == "A3_FILE":
                prompt = f"Vrať obsah souboru PATH={path}. Pokud je dlouhý, vrať chunk CHUNK_INDEX={chunk_index}."
            else:
                prompt = f"Vrať výsledný obsah souboru PATH={path} ACTION={action}. Pokud je dlouhý, vrať chunk CHUNK_INDEX={chunk_index}."
            prompt = self._append_io_reference(prompt, gen_ref_files)
            if extra_prompt:
                prompt += "\n\n" + str(extra_prompt)
            prompt = self._with_diag_text(prompt)

            payload = self._payload_base(
                stage=stage,
                instructions=instructions,
                input_parts=self._input_parts(prompt, gen_input_files, gen_input_images),
                prev_id=prev_id,
            )

            # deterministic file output
            if step_caps.get("supports_temperature", True):
                payload["temperature"] = 0.0

            if tools:
                payload["tools"] = tools
            vs_ids = []
            if tools:
                for t in tools:
                    if isinstance(t, dict) and t.get("type") == "file_search":
                        vs_ids = list(t.get("vector_store_ids") or [])
                        break
            self._log_request_attachments(contract, gen_ref_files, gen_input_files, gen_input_images, vs_ids, tools)

            self.log.save_json(
                "requests",
                f"{contract}_{path.replace('/','_')}_{chunk_index}_{ts_code()}",
                {
                    "payload": payload,
                    "ui_state": self._ui_state_for_log(),
                    **self._request_model_fields(),
                    "attachments": self._attachments_snapshot(contract, gen_ref_files, gen_input_files, gen_input_images, vs_ids, tools),
                },
            )
            self._log_api_action(
                f"{contract}:{path}",
                "send",
                {"chunk_index": chunk_index, "contract": contract, "path": path},
            )
            attempt = 0
            max_attempts = 3
            parsed = None
            last_err: Optional[Exception] = None
            while attempt < max_attempts and parsed is None:
                resp = with_retry(lambda: client.create_response(payload), self.settings.retry, self.breaker)
                self.log.save_json("responses", f"{contract}_{resp.get('id','NOID')}_{path.replace('/','_')}_{chunk_index}_{ts_code()}", resp)
                self._log_api_action(
                    f"{contract}:{path}",
                    "receive",
                    {
                        "chunk_index": chunk_index,
                        "response_id": resp.get("id"),
                        "attempt": attempt + 1,
                        "contract": contract,
                        "path": path,
                    },
                )
                try:
                    parsed = parse_json_strict(extract_text_from_response(resp))
                    if parsed.get("contract") != contract:
                        raise ContractError(f"{contract} mismatch (got {parsed.get('contract')})")
                except Exception as e:
                    last_err = e
                    parsed = None
                    attempt += 1
                    # detect invalid previous_response_id from API and stop promptly
                    if "previous_response_id" in str(e).lower():
                        self._last_prev_id_error = "Response ID je neplatné nebo expirované (API odmítlo previous_response_id). Ukončuji RUN."
                        raise
                    if attempt >= max_attempts:
                        # log and give up on this chunk but continue run
                        self._log_debug(f"{contract} {path} chunk {chunk_index}: invalid/mismatched response after {attempt} attempts: {e}")
                        try:
                            self.log.event("contract.mismatch", {"contract": contract, "path": path, "chunk": chunk_index, "error": str(e)})
                        except Exception:
                            pass
                        break
                    self._log_debug(f"{contract} {path} chunk {chunk_index}: invalid JSON/contract, retrying ({attempt}/{max_attempts})")
                    continue

            if parsed is None:
                # give up gracefully
                parts.append("")
                break

            parts.append(parsed.get("content", ""))
            ch = parsed.get("chunking", {}) or {}
            resp_id = str(resp.get("id") or "")
            if resp_id:
                latest_response_id = resp_id
                prev_id = resp_id
            self._log_api_action(
                f"{contract}:{path}",
                "complete",
                {
                    "chunk_index": chunk_index,
                    "response_id": resp_id,
                    "contract": contract,
                },
            )
            if not ch.get("has_more"):
                break

            chunk_index = int(ch.get("next_chunk_index") or (chunk_index + 1))
            if chunk_index > 5000:
                raise ContractError("Chunk loop guard")

        return "".join(parts), latest_response_id
