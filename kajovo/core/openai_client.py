from __future__ import annotations

import os
import json
import re
import time
import logging
from typing import Any, Dict, List, Optional
import requests
from .request_rules import validate_response_payload, validate_vector_attributes
from .compat import is_compatible_path
from .openai_transport import (
    OpenAIError,
    OpenAITransport,
    operation_spec,
)


def image_batch_submit_payload(
    input_file_id: str,
    endpoint: str,
) -> dict[str, Any]:
    if (
        not isinstance(input_file_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", input_file_id)
    ):
        raise ValueError("Obrazový BATCH vyžaduje platné input_file_id.")
    if endpoint not in {"/v1/images/generations", "/v1/images/edits"}:
        raise ValueError("Obrazový BATCH má nepovolený řádkový endpoint.")
    return {
        "input_file_id": input_file_id,
        "endpoint": endpoint,
        "completion_window": "24h",
        "output_expires_after": {
            "anchor": "created_at",
            "seconds": 2592000,
        },
    }


class OpenAIClient:
    @staticmethod
    def _validate_resource_id(identifier: str) -> None:
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("Identifikátor API nesmí obsahovat cestu, dotaz ani jiné oddělovače URL.")

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", timeout_s: float = 300.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.max_attempts = 4
        self.backoff_base_s = 0.8
        self.backoff_cap_s = 8.0
        self._sdk = None
        self._known_responses = set()
        try:
            from openai import OpenAI
            self._sdk = OpenAI(api_key=api_key, base_url=self.base_url, timeout=self.timeout_s, max_retries=0)
        except (ImportError, ValueError, TypeError) as exc:
            logging.getLogger(__name__).warning("SDK není dostupné, používám REST: %s", type(exc).__name__)
            self._sdk = None

        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})
        self._transport = OpenAITransport(
            base_url=self.base_url,
            api_key=api_key,
            timeout_s=self.timeout_s,
            session=self.session,
            backoff_base_s=self.backoff_base_s,
            backoff_cap_s=self.backoff_cap_s,
        )

    def _req(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict[str, Any]] = None,
        files=None,
        timeout: Optional[float] = None,
        max_attempts=None,
    ) -> Any:
        """Kompatibilitni fasada; retry rozhoduje vyhradne OpenAITransport."""
        spec = operation_spec(method, path)
        return self._transport.request(
            spec,
            method,
            path,
            json_body=json_body,
            files=files,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    def create_image(self, endpoint, body):
        """Jediný pracovní obrazový POST; neurčitý výsledek se neopakuje."""
        from .image_runtime import validate_image_request
        validate_image_request(endpoint, body)
        return self._req("POST", endpoint.removeprefix("/v1"), json_body=body, max_attempts=1)

    def create_image_batch(self, input_file_id, rows):
        from .image_runtime import validate_image_request
        self._validate_resource_id(input_file_id)
        ids, models, endpoints = set(), set(), set()
        if not 1 <= len(rows) <= 50000:
            raise ValueError("Obrazová dávka vyžaduje 1 až 50 000 položek.")
        for row in rows:
            cid = row.get("custom_id")
            self._validate_resource_id(cid)
            if cid in ids or row.get("method") != "POST":
                raise ValueError("Neplatná nebo duplicitní položka dávky.")
            ids.add(cid)
            validate_image_request(row["url"], row["body"])
            endpoints.add(row["url"])
            models.add(row["body"]["model"])
        if len(models) != 1 or len(endpoints) != 1:
            raise ValueError("Dávka musí mít jeden model a endpoint.")
        return self._req(
            "POST",
            "/batches",
            json_body=image_batch_submit_payload(
                input_file_id, next(iter(endpoints))
            ),
            max_attempts=1,
        )

    def list_models(self) -> List[Dict[str, Any]]:
        data = self._req("GET", "/models")
        return data.get("data", [])

    def list_files(self) -> List[Dict[str, Any]]:
        return self._list_all("/files")

    def upload_file(self, path: str, purpose: str = "user_data") -> Dict[str, Any]:
        if purpose == "batch":
            if os.path.getsize(path) > 200_000_000:
                raise ValueError("Dávka překračuje 200 MB.")
            with open(path, "rb") as stream:
                self.validate_batch_data(stream.read())
        with open(path, "rb") as stream:
            files = {"file": (os.path.basename(path), stream)}
            data = {"purpose": purpose}
            return self._req("POST", "/files", json_body=data, files=files)

    def delete_file(self, file_id: str) -> Dict[str, Any]:
        self._validate_resource_id(file_id)
        return self._req("DELETE", f"/files/{file_id}")

    def file_content(self, file_id: str) -> bytes:
        self._validate_resource_id(file_id)
        return self._req("GET", f"/files/{file_id}/content")

    def retrieve_file(self, file_id: str) -> Dict[str, Any]:
        self._validate_resource_id(file_id)
        return self._req("GET", f"/files/{file_id}")

    def configure_validation(self, settings):
        from .response_policy import ResponsePolicy

        self._policy = ResponsePolicy(self, settings.cache_dir, settings.log_dir)

    def validate_access(self, payload, batch=False):
        """Provede lokální kontrolu payloadu bez generativního požadavku."""
        from .response_policy import ResponsePolicy

        if not hasattr(self, "_policy"):
            self._policy = ResponsePolicy(self)
        self._policy.ensure(payload, batch, preparing=True)

    def validate_prepared_payload(self, payload, batch=False):
        """Lokálně ověří připravený payload; nikdy neodesílá zkušební Response."""
        from .response_policy import ResponsePolicy

        if not hasattr(self, "_policy"):
            self._policy = ResponsePolicy(self)
        self._policy.ensure(payload, batch, preparing=True)

    def validate_previous_response(self, response_id):
        self._validate_resource_id(response_id)
        if response_id in self._known_responses:
            return
        response = self._req("GET", f"/responses/{response_id}")
        if not isinstance(response, dict) or response.get("status") != "completed":
            raise ValueError("Předchozí odpověď není dostupná a dokončená.")
        self._known_responses.add(response_id)

    def validate_resources(self, payload):
        """Ověří skutečné reference ne-generativními čteními, i po změně klíče."""
        from .compat import (
            validate_input_file_sizes,
            SUPPORTED_INPUT_FILE_EXTS,
            SUPPORTED_INPUT_IMAGE_EXTS,
        )
        from .model_registry import model_spec

        if payload.get("previous_response_id"):
            self.validate_previous_response(payload["previous_response_id"])
        metadata = []
        spec = model_spec(payload["model"])
        for message in payload.get("input", []) if isinstance(payload.get("input"), list) else []:
            parts = message.get("content", [])
            for part in parts if isinstance(parts, list) else []:
                if part.get("file_id"):
                    item = self.retrieve_file(part["file_id"])
                    metadata.append(item)
                    extension = os.path.splitext(str(item.get("filename", "")))[1].lower()
                    allowed = (
                        SUPPORTED_INPUT_IMAGE_EXTS
                        if part["type"] == "input_image"
                        else SUPPORTED_INPUT_FILE_EXTS
                    )
                    if extension not in allowed:
                        raise ValueError(
                            f"{part['type']}.file_id: nepodporovaný formát {extension or '(bez přípony)'} ."
                        )
                    if (
                        part["type"] == "input_file"
                        and str(item.get("filename", "")).lower().endswith(".pdf")
                        and "image_input" not in spec["features"]
                    ):
                        raise ValueError(
                            f"{payload['model']}: PDF file_id vyžaduje model s podporou obrázků."
                        )
                elif part.get("file_data"):
                    import base64

                    metadata.append(
                        {
                            "bytes": len(
                                base64.b64decode(
                                    part["file_data"].split(",")[-1], validate=True
                                )
                            )
                        }
                    )
        if metadata:
            validate_input_file_sizes(metadata)
        for tool in payload.get("tools", []):
            for vs_id in tool.get("vector_store_ids", []):
                self._validate_resource_id(vs_id)
                store = self._req("GET", f"/vector_stores/{vs_id}")
                counts = store.get("file_counts") or {}
                if (
                    store.get("status") != "completed"
                    or counts.get("in_progress", 0)
                    or counts.get("failed", 0)
                    or counts.get("cancelled", 0)
                ):
                    raise ValueError(
                        f"file_search.vector_store_ids: {vs_id} není kompletně zpracované úložiště."
                    )

    def prepare_run_validation(self, cfg):
        """Připraví lokální modelové capability snímky bez placeného probe volání."""
        from .structured_output import text_format
        from .request_rules import uses_reasoning_defaults
        from .requirements import apply_quality
        from .model_capabilities import ModelCapabilitiesCache

        models = [cfg.model]
        if cfg.mode == "GENERATE":
            models += [
                getattr(cfg, field, "") or cfg.model
                for field in ("model_a1", "model_a2", "model_a3")
            ]
        preparation_models = set(models)
        if cfg.mode == "GENERATE" and cfg.send_as_c:
            preparation_models = {
                getattr(cfg, field, "") or cfg.model for field in ("model_a1", "model_a2")
            }
        caps_by_model = {}
        for model in dict.fromkeys(models):
            payload = {"model": model, "input": "lokální kontrola", "text": text_format()}
            if not uses_reasoning_defaults(model):
                payload["temperature"] = cfg.temperature
            if cfg.mode in ("GENERATE", "MODIFY"):
                # Placeholder je pouze pro lokální kontrolu tvaru payloadu;
                # nikdy se neodesílá na /responses.
                payload["previous_response_id"] = "resp_local_validation"
            if cfg.attached_vector_store_ids and model in preparation_models:
                payload["tools"] = [
                    {"type": "file_search", "vector_store_ids": cfg.attached_vector_store_ids}
                ]
            if cfg.mode in ("GENERATE", "MODIFY"):
                apply_quality(payload, getattr(cfg, "maximum_quality", False))
            self.validate_prepared_payload(payload)
            typed = ModelCapabilitiesCache("").get(model)
            if typed is None or not typed.ok_basic:
                raise ValueError(f"Model {model} není povolen v capability registru.")
            caps = typed.to_dict()
            caps.update({
                "supports_previous_response_id": bool(payload.get("previous_response_id")),
                "supports_temperature": not uses_reasoning_defaults(model),
            })
            caps_by_model[model] = caps
        cfg.available_models = list(self._policy.catalog)
        cfg.caps_by_model = caps_by_model
        cfg.model_caps = caps_by_model[cfg.model]
        if cfg.send_as_c:
            batch_model = (
                (getattr(cfg, "model_a3", "") or cfg.model)
                if cfg.mode == "GENERATE"
                else cfg.model
            )
            self.validate_prepared_payload(
                {"model": batch_model, "input": "lokální kontrola", "text": text_format()},
                batch=True,
            )

    def count_input_tokens(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Samostatné ne-generativní měření; neodesílá pracovní Response."""
        from .context_compiler import content_hash
        allowed = {"model", "input", "instructions", "previous_response_id", "text", "tools",
                   "tool_choice", "parallel_tool_calls", "reasoning", "truncation"}
        result = self._req("POST", "/responses/input_tokens",
                           json_body={k: v for k, v in payload.items() if k in allowed}, max_attempts=1)
        count = result.get("input_tokens") if isinstance(result, dict) else None
        if type(count) is not int or count < 0:
            raise OpenAIError("Token count endpoint nevrátil platný počet tokenů.")
        return {"input_tokens": count, "request_hash": content_hash(payload), "method": "responses.input_tokens"}

    def create_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from .structured_output import validate_output
        from .contracts import ContractError

        try:
            self.validate_access(payload)
            self._policy.ensure(payload)
        except Exception as exc:
            exc.request_sent = False
            raise
        response = self._send_response(payload)
        if payload.get("background"):
            return response
        try:
            validate_output(response, payload)
        except ContractError as exc:
            exc.response = response
            raise
        return response

    def _response_operation(self, response_id, *, cancel=False):
        self._validate_resource_id(response_id)
        if cancel:
            return self._req("POST", f"/responses/{response_id}/cancel", max_attempts=1)
        return self._req("GET", f"/responses/{response_id}")

    def retrieve_response(self, response_id):
        return self._response_operation(response_id)

    def cancel_response(self, response_id):
        return self._response_operation(response_id, cancel=True)

    def _send_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from .structured_output import prepare_payload

        prepare_payload(payload)
        validate_response_payload(payload)
        started = time.monotonic()
        try:
            result = self._req("POST", "/responses", json_body=payload, timeout=self.timeout_s)
            if payload.get("store", True) and result.get("status") == "completed" and result.get("id"):
                self._known_responses.add(result["id"])
            return result
        except OpenAIError as exc:
            exc.elapsed_s = time.monotonic() - started
            exc.phase = "response"
            if exc.status_code in (400, 422) and hasattr(self, "_policy"):
                self._policy.invalidate(payload["model"])
            raise

    def list_vector_stores(self) -> List[Dict[str, Any]]:
        return self._list_all("/vector_stores")

    def _list_all(self, path: str) -> List[Dict[str, Any]]:
        from urllib.parse import urlencode

        rows = []
        cursor = None
        seen = set()
        while True:
            query = {"limit": 100}
            if cursor:
                query["after"] = cursor
            page = self._req("GET", path + "?" + urlencode(query))
            data = page.get("data", [])
            rows.extend(data)
            if not page.get("has_more"):
                return rows
            cursor = page.get("last_id") or (data[-1].get("id") if data else None)
            if not cursor or cursor in seen:
                raise OpenAIError("Neplatné stránkování API odpovědi.")
            seen.add(cursor)

    def create_vector_store(self, name: str, expires_after_days: Optional[int]=None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name}
        if expires_after_days is not None:
            body["expires_after"] = {"anchor": "last_active_at", "days": int(expires_after_days)}
        return self._req("POST", "/vector_stores", json_body=body)

    def add_file_to_vector_store(self, vs_id: str, file_id: str, attributes: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        self._validate_resource_id(vs_id)
        self._validate_resource_id(file_id)
        if attributes is not None:
            validate_vector_attributes(attributes)
        metadata = self.retrieve_file(file_id)
        if not is_compatible_path(str(metadata.get("filename") or "")):
            raise ValueError("Formát souboru není podporován pro file search.")
        body: Dict[str, Any] = {"file_id": file_id}
        if attributes:
            body["attributes"] = attributes
        return self._req("POST", f"/vector_stores/{vs_id}/files", json_body=body, timeout=120.0)

    def retrieve_vector_store(self, vs_id: str) -> Dict[str, Any]:
        self._validate_resource_id(vs_id)
        return self._req("GET", f"/vector_stores/{vs_id}")

    def delete_vector_store(self, vs_id: str) -> Dict[str, Any]:
        self._validate_resource_id(vs_id)
        return self._req("DELETE", f"/vector_stores/{vs_id}")

    def list_vector_store_files(self, vs_id: str) -> List[Dict[str, Any]]:
        self._validate_resource_id(vs_id)
        return self._list_all(f"/vector_stores/{vs_id}/files")

    def retrieve_vector_store_file(self, vs_id: str, vector_store_file_id: str) -> Dict[str, Any]:
        self._validate_resource_id(vs_id)
        self._validate_resource_id(vector_store_file_id)
        return self._req("GET", f"/vector_stores/{vs_id}/files/{vector_store_file_id}")

    def delete_vector_store_file(self, vs_id: str, vector_store_file_id: str) -> Dict[str, Any]:
        self._validate_resource_id(vs_id)
        self._validate_resource_id(vector_store_file_id)
        return self._req("DELETE", f"/vector_stores/{vs_id}/files/{vector_store_file_id}")

    def update_vector_store_file_attributes(self, vs_id: str, vector_store_file_id: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
        self._validate_resource_id(vs_id)
        self._validate_resource_id(vector_store_file_id)
        validate_vector_attributes(attributes)
        body = {"attributes": attributes}
        return self._req("POST", f"/vector_stores/{vs_id}/files/{vector_store_file_id}", json_body=body)

    def list_batches(self) -> List[Dict[str, Any]]:
        return self._list_all("/batches")

    def create_batch(
        self,
        input_file_id: str,
        endpoint: str = "/v1/responses",
        completion_window: str = "24h",
        *,
        _prevalidated_rows=None,
    ) -> Dict[str, Any]:
        """Vytvoří jedinou skutečnou pracovní dávku.

        Běžné volání znovu načte a lokálně ověří vzdálený JSONL. Interní
        work-submit cesta dostane již ověřené řádky a před jediným POST /batches
        neprovádí žádnou další síťovou operaci.
        """
        self._validate_resource_id(input_file_id)
        if endpoint != "/v1/responses" or completion_window != "24h":
            raise ValueError("Program podporuje pouze Batch Responses s oknem 24h.")
        if _prevalidated_rows is None:
            rows = self.validate_batch_data(self.file_content(input_file_id))
        else:
            rows = list(_prevalidated_rows)
            if not rows or not all(
                isinstance(row, dict) and isinstance(row.get("body"), dict) for row in rows
            ):
                raise ValueError("Interní work submit vyžaduje již ověřené řádky dávky.")
        from .batch_submit import response_batch_submit_payload

        body = response_batch_submit_payload(
            input_file_id,
            endpoint=endpoint,
            completion_window=completion_window,
        )
        return self._req("POST", "/batches", json_body=body)

    def validate_batch_data(self, data):
        from .structured_output import prepare_payload

        if not isinstance(data, bytes) or not data or len(data) > 200_000_000:
            raise ValueError("Neplatná velikost dávky.")
        rows = [
            json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()
        ]
        if not 1 <= len(rows) <= 50000:
            raise ValueError("Dávka vyžaduje 1 až 50000 požadavků.")
        image_endpoints = {"/v1/images/generations", "/v1/images/edits"}
        if any(isinstance(row, dict) and row.get("url") in image_endpoints for row in rows):
            from .image_runtime import image_capability, validate_image_request
            ids, endpoints, image_models = set(), set(), set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"custom_id", "method", "url", "body"}:
                    raise ValueError("Neplatný řádek obrazové dávky.")
                self._validate_resource_id(row["custom_id"])
                if row["custom_id"] in ids or row["method"] != "POST" or row["url"] not in image_endpoints:
                    raise ValueError("Neplatné ID nebo endpoint obrazové dávky.")
                validate_image_request(row["url"], row["body"])
                if not image_capability(row["body"]["model"])["batch"]:
                    raise ValueError("Model nepodporuje obrazový Batch.")
                ids.add(row["custom_id"])
                endpoints.add(row["url"])
                image_models.add(row["body"]["model"])
            if len(endpoints) != 1 or len(image_models) != 1:
                raise ValueError("Obrazový dávkový soubor vyžaduje jeden endpoint a model.")
            return rows
        seen = set()
        models = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "custom_id",
                "method",
                "url",
                "body",
            }:
                raise ValueError("Neplatný řádek dávky.")
            cid = row["custom_id"]
            if (
                not isinstance(cid, str)
                or not cid
                or cid in seen
                or row["method"] != "POST"
                or row["url"] != "/v1/responses"
            ):
                raise ValueError("Neplatné nebo duplicitní ID či endpoint dávky.")
            seen.add(cid)
            if not isinstance(row["body"], dict) or "text" not in row["body"]:
                raise ValueError("Dávkový požadavek postrádá povinné JSON Schema.")
            if row["body"].get("previous_response_id") or row["body"].get("conversation"):
                raise ValueError(
                    "Dávkové soubory musí obsahovat samostatné zadání bez návaznosti."
                )
            prepare_payload(row["body"])
            validate_response_payload(row["body"], batch=True)
            models.add(row["body"]["model"])
        if len(models) != 1:
            raise ValueError("Jeden dávkový soubor smí obsahovat pouze jediný model.")
        for row in rows:
            self.validate_access(row["body"], batch=True)
        self._policy.ensure_batch([row["body"] for row in rows])
        return rows

    def retrieve_batch(self, batch_id: str) -> Dict[str, Any]:
        self._validate_resource_id(batch_id)
        return self._req("GET", f"/batches/{batch_id}")

    def cancel_batch(self, batch_id: str) -> Dict[str, Any]:
        self._validate_resource_id(batch_id)
        return self._req("POST", f"/batches/{batch_id}/cancel", json_body={})
