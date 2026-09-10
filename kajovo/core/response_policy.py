"""Dokumentované možnosti a průkazné ověření přístupu konkrétního účtu."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import requests

from .structured_output import prepare_payload, text_format, validate_output
from .utils import atomic_write_text


TTL = 24 * 3600


class ProbeTransport:
    """Oddělený transport pouze pro interní ověření, bez rekurzivního probe."""
    def __init__(self, client):
        self.client = client

    def validate_access(self, payload, batch=False):
        from .request_rules import validate_response_payload
        prepare_payload(payload)
        validate_response_payload(payload)

    def count_input_tokens(self, payload):
        return self.client.count_input_tokens(payload, _probe=True)

    def create_response(self, payload):
        self.validate_access(payload)
        return self.client._send_response(payload)


class ResponsePolicy:
    def __init__(self, client, cache_dir="cache", log_dir="LOG"):
        self.client = client
        self.identity = hashlib.sha256(client.base_url.rstrip("/").encode()).hexdigest()
        self.path = Path(cache_dir) / "response_validation" / (self.identity + ".json")
        self.log_dir = Path(log_dir)
        self.catalog = None
        self.proofs = {}
        self.docs = {}
        try:
            root = json.loads(self.path.read_text(encoding="utf-8"))
            if root.get("version") == 2 and root.get("identity") == self.identity:
                self.proofs = root.get("proofs", {})
                self.docs = root.get("docs", {})
        except (OSError, ValueError, TypeError):
            pass
        if not self.path.exists() and client.base_url.rstrip("/") == "https://api.openai.com/v1":
            self._migrate_legacy()

    def _migrate_legacy(self):
        """Převezme starší úspěchy výchozího endpointu bez změny data ověření."""
        for path in sorted(self.path.parent.glob("*.json")):
            try:
                root = json.loads(path.read_text(encoding="utf-8"))
                if root.get("version") != 1:
                    continue
                for key, proof in root.get("proofs", {}).items():
                    if proof.get("state") != "verified":
                        continue
                    if proof.get("tested_at", 0) > self.proofs.get(key, {}).get("tested_at", 0):
                        self.proofs[key] = proof
                for model, spec in root.get("docs", {}).items():
                    if spec.get("checked_at", 0) > self.docs.get(model, {}).get("checked_at", 0):
                        self.docs[model] = spec
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        self.save()
        for model in {p["model"] for p in self.proofs.values()}:
            self.publish_model(model)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(str(self.path), json.dumps({"version": 2, "identity": self.identity,
            "proofs": self.proofs, "docs": self.docs}, ensure_ascii=False))

    def invalidate(self, model):
        self.proofs = {key: value for key, value in self.proofs.items() if value.get("model") != model}
        self.catalog = None
        self.save()

    def model_spec(self, model):
        cached = self.docs.get(model)
        if cached and 0 <= time.time() - cached["checked_at"] < TTL:
            return cached
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", model):
            raise ValueError("Neplatný identifikátor modelu.")
        candidates = [model]
        base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
        if base != model:
            candidates.append(base)
        for candidate in candidates:
            url = f"https://developers.openai.com/api/docs/models/{candidate}.md"
            response = requests.get(url, timeout=20)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            raw = response.text
            if f"`{model}`" not in raw:
                continue
            max_tokens = re.search(r"([\d,]+) max output tokens", raw)
            context = re.search(r"([\d,]+) context window", raw)
            if not max_tokens or not context:
                continue
            spec = {"checked_at": time.time(), "source": url,
                "features": re.findall(r"^- ([a-z][a-z_]+)\s*$", raw, re.M),
                "responses": bool(re.search(r"\| Responses \|[^\n]+\| Supported \|", raw)),
                "batch": bool(re.search(r"\| Batch \|[^\n]+\| Supported \|", raw)),
                "max_output_tokens": int(max_tokens[1].replace(",", "")),
                "context_window": int(context[1].replace(",", "")),
                "reasoning": sorted(set(re.findall(r"\b(none|minimal|low|medium|high|xhigh)\b", raw.split("## Model details")[0])))}
            self.docs[model] = spec
            self.save()
            return spec
        raise ValueError(f"{model}: nepodařilo se ověřit aktuální specifikaci modelu.")

    def check_documented(self, payload, batch=False):
        from .request_rules import validate_response_payload
        prepare_payload(payload)
        validate_response_payload(payload)
        model = payload["model"]
        if self.catalog is None:
            self.catalog = {row["id"] for row in self.client.list_models()}
        if model not in self.catalog:
            raise ValueError(f"{model}: model není v aktuálním katalogu účtu.")
        spec = self.model_spec(model)
        if not spec["responses"] or "structured_outputs" not in spec["features"]:
            self.mark_unavailable(model, "Dokumentace nepotvrzuje Responses se Structured Outputs.")
            raise ValueError(f"{model}: není doložena podpora Responses s JSON Schema.")
        if batch and not spec["batch"]:
            raise ValueError(f"{model}: nepodporuje Batch.")
        effort = (payload.get("reasoning") or {}).get("effort")
        if effort and effort not in spec["reasoning"]:
            raise ValueError(f"{model}: nepodporované reasoning.effort={effort}.")
        if payload.get("max_output_tokens", 16) > spec["max_output_tokens"]:
            raise ValueError(f"{model}: překročen limit výstupních tokenů.")
        for tool in payload.get("tools", []):
            if tool["type"] not in spec["features"]:
                raise ValueError(f"{model}: nepodporovaný nástroj {tool['type']}.")
        for message in payload.get("input", []) if isinstance(payload.get("input"), list) else []:
            parts = message.get("content", [])
            for part in parts if isinstance(parts, list) else []:
                required = {"input_image": "image_input", "input_file": "file_uploads"}.get(part.get("type"))
                if required and required not in spec["features"]:
                    raise ValueError(f"{model}: nepodporovaný vstup {part['type']}.")
        return spec

    def ensure(self, payload, batch=False, preparing=False):
        self.check_documented(payload, batch)
        if payload.get("previous_response_id") and not preparing:
            self.client.validate_previous_response(payload["previous_response_id"])
        combo = {k: payload[k] for k in ("model", "reasoning", "temperature", "top_p", "tools", "tool_choice") if k in payload}
        combo["previous_response_id"] = bool(payload.get("previous_response_id"))
        combo["batch"] = batch
        key = hashlib.sha256(json.dumps(combo, sort_keys=True).encode()).hexdigest()
        proof = self.proofs.get(key)
        if proof and 0 <= time.time() - proof["tested_at"] < TTL:
            if proof["state"] == "verified":
                return
            if proof["state"] == "unsupported":
                raise ValueError(f"{payload['model']}: tato kombinace byla API odmítnuta: {proof.get('error', '')}")
        probe = {k: copy.deepcopy(v) for k, v in combo.items() if k not in ("batch", "previous_response_id")}
        probe.update(input="Odpověz krátce: OK.", text=text_format(), max_output_tokens=min(2048, self.docs[payload["model"]]["max_output_tokens"]))
        if probe.get("tools"):
            probe["tool_choice"] = {"type": "file_search"}
            probe["input"] = "Použij file_search pro vyhledání obsahu úložiště a stručně potvrď výsledek."
        try:
            if combo["previous_response_id"]:
                first = self.probe(probe)
                probe["previous_response_id"] = first["id"]
            result = self.probe(probe)
            self.proofs[key] = {"model": payload["model"], "state": "verified", "tested_at": time.time(),
                "combination": combo, "response_id": result["id"], "request_id": result.get("_request_id")}
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            self.proofs[key] = {"model": payload["model"], "state": "unsupported" if status in (400, 422) else "unknown",
                "tested_at": time.time(), "combination": combo, "error": str(exc)}
            self.save()
            if status in (403, 404) and ("model" in str(exc).lower() or not probe.get("tools")):
                self.mark_unavailable(payload["model"], str(exc))
            raise
        self.save()
        self.publish_model(payload["model"])

    def mark_unavailable(self, model, reason):
        from .model_capabilities import ModelCapabilities, ModelCapabilitiesCache
        cache = ModelCapabilitiesCache(str(self.path.parent.parent / "model_capabilities.json"))
        cache.bind(self.client.api_key, self.client.base_url)
        cache.upsert(ModelCapabilities(model, time.time(), False, False, False, False, False,
            notes="Model není dostupný pro pracovní kontrakty.", errors={"basic": reason}))
        cache.save()

    def publish_model(self, model):
        from .model_capabilities import ModelCapabilities, ModelCapabilitiesCache
        cache = ModelCapabilitiesCache(str(self.path.parent.parent / "model_capabilities.json"))
        cache.bind(self.client.api_key, self.client.base_url)
        verified = [p for p in self.proofs.values() if p.get("model") == model and p["state"] == "verified"
                    and 0 <= time.time() - p["tested_at"] < TTL]
        if not verified:
            return
        combinations = [p["combination"] for p in verified]
        fs = any(c.get("tools") for c in combinations)
        cache.upsert(ModelCapabilities(model, max(p["tested_at"] for p in verified), True,
            any(c.get("previous_response_id") for c in combinations), any("temperature" in c for c in combinations),
            fs, fs, fs, True, "Ověřeno automatickou přípravou běhu."))
        cache.save()

    def probe(self, payload):
        """Ověření má vlastní stopu i při chybě; nejde o pracovní požadavek."""
        from .runlog import RunLogger
        log = RunLogger(str(self.log_dir), "PROBE_" + uuid.uuid4().hex, "Ověření konfigurace")
        log.save_json("requests", "probe", payload)
        try:
            adapter = ProbeTransport(self.client)
            controller = getattr(self.client, "cost_control", None)
            result = controller.execute(adapter, payload, stage="PROBE") if controller else adapter.create_response(payload)
            log.save_json("responses", "probe", result)
            if controller is None and getattr(self, "db_path", None):
                from .receipt import Receipt, ReceiptDB
                usage = result.get("usage") or {}
                ReceiptDB(self.db_path).insert(Receipt(log.run_id, time.time(), "Ověření konfigurace", result.get("model") or payload["model"],
                    "PROBE", "PROBE", result.get("id"), None, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                    None, None, None, False, "Spotřeba probe; cena nebyla potvrzena ceníkem.", {"run_dir": log.paths.run_dir}, usage))
            validate_output(result, payload)
            for tool in payload.get("tools", []):
                if tool["type"] == "file_search" and not any(item.get("type") == "file_search_call" for item in result.get("output", [])):
                    raise ValueError("Probe neprokázal skutečné použití file search.")
            log.update_state({"status": "completed", "model": payload["model"], "response_id": result.get("id")})
            return result
        except Exception as exc:
            if isinstance(getattr(exc, "response", None), dict):
                log.save_json("responses", "rejected_output", exc.response)
            log.update_state({"status": "failed", "error": str(exc), "model": payload["model"]})
            raise
