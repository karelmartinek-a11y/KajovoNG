from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
import requests

class OpenAIError(Exception):
    pass

class OpenAIClient:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._sdk = None
        try:
            from openai import OpenAI  # type: ignore
            self._sdk = OpenAI(api_key=api_key)
        except Exception:
            self._sdk = None

        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})

    def _req(self, method: str, path: str, json_body: Optional[Dict[str, Any]]=None, files=None, timeout: float=60.0) -> Any:
        url = self.base_url + path
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if files is None:
            headers["Content-Type"] = "application/json"
            r = self.session.request(method, url, headers=headers, json=json_body, timeout=timeout)
        else:
            r = requests.request(method, url, headers={"Authorization": f"Bearer {self.api_key}"}, data=json_body, files=files, timeout=timeout)
        if r.status_code >= 400:
            raise OpenAIError(f"{method} {path} -> {r.status_code}: {r.text[:4000]}")
        if r.headers.get("content-type","").startswith("application/json"):
            return r.json()
        return r.content

    def list_models(self) -> List[Dict[str, Any]]:
        if self._sdk is not None:
            try:
                return [m.model_dump() for m in self._sdk.models.list().data]  # type: ignore
            except Exception:
                pass
        data = self._req("GET", "/models")
        return data.get("data", [])

    def list_files(self) -> List[Dict[str, Any]]:
        if self._sdk is not None:
            try:
                return [f.model_dump() for f in self._sdk.files.list().data]  # type: ignore
            except Exception:
                pass
        data = self._req("GET", "/files")
        return data.get("data", [])

    def upload_file(self, path: str, purpose: str = "user_data") -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                with open(path, "rb") as f:
                    obj = self._sdk.files.create(file=f, purpose=purpose)  # type: ignore
                return obj.model_dump()  # type: ignore
            except Exception:
                pass
        with open(path, "rb") as f:
            files = {"file": (os.path.basename(path), f)}
            data = {"purpose": purpose}
            return self._req("POST", "/files", json_body=data, files=files)

    def delete_file(self, file_id: str) -> Dict[str, Any]:
        if self._sdk is not None:
            try:
                obj = self._sdk.files.delete(file_id)  # type: ignore
                return obj.model_dump()  # type: ignore
            except Exception:
                pass
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
            except Exception:
                pass
        return self._req("POST", "/responses", json_body=payload, timeout=120.0)

    def list_vector_stores(self) -> List[Dict[str, Any]]:
        data = self._req("GET", "/vector_stores")
        return data.get("data", [])

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
        data = self._req("GET", f"/vector_stores/{vs_id}/files")
        return data.get("data", [])

    def retrieve_vector_store_file(self, vs_id: str, vector_store_file_id: str) -> Dict[str, Any]:
        return self._req("GET", f"/vector_stores/{vs_id}/files/{vector_store_file_id}")

    def delete_vector_store_file(self, vs_id: str, vector_store_file_id: str) -> Dict[str, Any]:
        return self._req("DELETE", f"/vector_stores/{vs_id}/files/{vector_store_file_id}")

    def update_vector_store_file_attributes(self, vs_id: str, vector_store_file_id: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
        body = {"attributes": attributes}
        return self._req("PATCH", f"/vector_stores/{vs_id}/files/{vector_store_file_id}", json_body=body)


    def list_batches(self) -> List[Dict[str, Any]]:
        data = self._req("GET", "/batches")
        return data.get("data", [])

    def create_batch(self, input_file_id: str, endpoint: str = "/v1/responses", completion_window: str = "24h") -> Dict[str, Any]:
        body = {"input_file_id": input_file_id, "endpoint": endpoint, "completion_window": completion_window}
        return self._req("POST", "/batches", json_body=body)

    def retrieve_batch(self, batch_id: str) -> Dict[str, Any]:
        return self._req("GET", f"/batches/{batch_id}")

    def cancel_batch(self, batch_id: str) -> Dict[str, Any]:
        return self._req("POST", f"/batches/{batch_id}/cancel", json_body={})
