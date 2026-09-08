from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional
import requests

class OpenAIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

class OpenAIClient:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", timeout_s: float = 60.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.max_attempts = 4
        self.backoff_base_s = 0.8
        self.backoff_cap_s = 8.0
        self._sdk = None
        try:
            from openai import OpenAI  # type: ignore
            self._sdk = OpenAI(api_key=api_key, base_url=self.base_url, timeout=self.timeout_s, max_retries=0)
        except Exception:
            self._sdk = None

        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def _should_retry(self, status_code: Optional[int], error: Optional[Exception]) -> bool:
        if error is not None:
            return isinstance(error, (requests.Timeout, requests.ConnectionError))
        if status_code is None:
            return False
        return status_code == 429 or 500 <= status_code < 600

    def _retry_delay(self, attempt: int, retry_after_header: str) -> float:
        try:
            if retry_after_header:
                return max(0.0, min(float(retry_after_header), self.backoff_cap_s))
        except Exception:
            pass
        return min(self.backoff_cap_s, self.backoff_base_s * (2 ** max(0, attempt - 1)))

    @staticmethod
    def _safe_err_excerpt(text: str, max_chars: int = 1200) -> str:
        if not text:
            return ""
        return text[:max_chars]

    def _req(self, method: str, path: str, json_body: Optional[Dict[str, Any]]=None, files=None, timeout: Optional[float]=None) -> Any:
        url = self.base_url + path
        req_timeout = float(timeout if timeout is not None else self.timeout_s)
        last_error: Optional[str] = None
        file_positions = []
        for value in (files or {}).values():
            stream = value[1] if isinstance(value, tuple) else value
            if hasattr(stream, "tell") and hasattr(stream, "seek"):
                file_positions.append((stream, stream.tell()))
        for attempt in range(1, self.max_attempts + 1):
            for stream, position in file_positions:
                stream.seek(position)
            r = None
            err: Optional[Exception] = None
            try:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                if files is None:
                    headers["Content-Type"] = "application/json"
                    r = self.session.request(method, url, headers=headers, json=json_body, timeout=req_timeout)
                else:
                    r = self.session.request(method, url, headers={"Authorization": f"Bearer {self.api_key}"}, data=json_body, files=files, timeout=req_timeout)
                if r.status_code >= 400:
                    excerpt = self._safe_err_excerpt(getattr(r, "text", ""))
                    if not self._should_retry(r.status_code, None) or attempt >= self.max_attempts:
                        raise OpenAIError(f"{method} {path} -> {r.status_code}: {excerpt}", status_code=r.status_code)
                    delay = self._retry_delay(attempt, str(r.headers.get("retry-after", "")))
                    time.sleep(delay)
                    continue
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return r.content
            except OpenAIError:
                raise
            except Exception as ex:
                err = ex
                last_error = str(ex)
                if not self._should_retry(None, err) or attempt >= self.max_attempts:
                    raise OpenAIError(f"{method} {path} failed: {self._safe_err_excerpt(last_error or '')}") from ex
                time.sleep(self._retry_delay(attempt, ""))
        raise OpenAIError(f"{method} {path} failed: {self._safe_err_excerpt(last_error or 'unknown error')}")

    def list_models(self) -> List[Dict[str, Any]]:
        if self._sdk is not None:
            try:
                return [m.model_dump() for m in self._sdk.models.list()]  # type: ignore
            except Exception:
                pass
        data = self._req("GET", "/models")
        return data.get("data", [])

    def list_files(self) -> List[Dict[str, Any]]:
        if self._sdk is not None:
            try:
                return [f.model_dump() for f in self._sdk.files.list()]  # type: ignore
            except Exception:
                pass
        return self._list_all("/files")

    def upload_file(self, path: str, purpose: str = "user_data") -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                with open(path, "rb") as f:
                    obj = self._sdk.files.create(file=f, purpose=purpose)  # type: ignore
                return obj.model_dump()  # type: ignore
            except Exception as exc:
                raise OpenAIError(str(exc)) from exc
        with open(path, "rb") as f:
            files = {"file": (os.path.basename(path), f)}
            data = {"purpose": purpose}
            return self._req("POST", "/files", json_body=data, files=files)

    def delete_file(self, file_id: str) -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                obj = self._sdk.files.delete(file_id)  # type: ignore
                return obj.model_dump()  # type: ignore
            except Exception as exc:
                raise OpenAIError(str(exc)) from exc
        return self._req("DELETE", f"/files/{file_id}")

    def file_content(self, file_id: str) -> bytes:
        if self._sdk is not None:
            try:
                return self._sdk.files.content(file_id).read()  # type: ignore
            except Exception:
                pass
        return self._req("GET", f"/files/{file_id}/content")

    def retrieve_file(self, file_id: str) -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                obj = self._sdk.files.retrieve(file_id)  # type: ignore
                return obj.model_dump()  # type: ignore
            except Exception:
                pass
        return self._req("GET", f"/files/{file_id}")

    def create_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                obj = self._sdk.responses.create(**payload)  # type: ignore
                return obj.model_dump()  # type: ignore
            except Exception as exc:
                raise OpenAIError(str(exc)) from exc
        return self._req("POST", "/responses", json_body=payload, timeout=120.0)

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
        body: Dict[str, Any] = {"file_id": file_id}
        if attributes:
            body["attributes"] = attributes
        return self._req("POST", f"/vector_stores/{vs_id}/files", json_body=body, timeout=120.0)

    

    def retrieve_vector_store(self, vs_id: str) -> Dict[str, Any]:
        return self._req("GET", f"/vector_stores/{vs_id}")

    def delete_vector_store(self, vs_id: str) -> Dict[str, Any]:
        return self._req("DELETE", f"/vector_stores/{vs_id}")

    def list_vector_store_files(self, vs_id: str) -> List[Dict[str, Any]]:
        return self._list_all(f"/vector_stores/{vs_id}/files")

    def retrieve_vector_store_file(self, vs_id: str, vector_store_file_id: str) -> Dict[str, Any]:
        return self._req("GET", f"/vector_stores/{vs_id}/files/{vector_store_file_id}")

    def delete_vector_store_file(self, vs_id: str, vector_store_file_id: str) -> Dict[str, Any]:
        return self._req("DELETE", f"/vector_stores/{vs_id}/files/{vector_store_file_id}")

    def update_vector_store_file_attributes(self, vs_id: str, vector_store_file_id: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
        body = {"attributes": attributes}
        return self._req("POST", f"/vector_stores/{vs_id}/files/{vector_store_file_id}", json_body=body)


    def list_batches(self) -> List[Dict[str, Any]]:
        return self._list_all("/batches")

    def create_batch(self, input_file_id: str, endpoint: str = "/v1/responses", completion_window: str = "24h") -> Dict[str, Any]:
        body = {"input_file_id": input_file_id, "endpoint": endpoint, "completion_window": completion_window}
        return self._req("POST", "/batches", json_body=body)

    def retrieve_batch(self, batch_id: str) -> Dict[str, Any]:
        return self._req("GET", f"/batches/{batch_id}")

    def cancel_batch(self, batch_id: str) -> Dict[str, Any]:
        return self._req("POST", f"/batches/{batch_id}/cancel", json_body={})
