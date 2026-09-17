from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: očekáván 1 výskyt, nalezeno {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"(?ms)^    def {re.escape(name)}\(.*?(?=^    def [A-Za-z_][A-Za-z0-9_]*\()"
    )
    match = pattern.search(text)
    if not match:
        raise RuntimeError(f"Funkce {name} nebyla nalezena.")
    return text[: match.start()] + replacement.rstrip() + "\n\n" + text[match.end() :]


def migrate_client() -> None:
    path = ROOT / "kajovo/core/openai_client.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    SubmissionOutcomeUnknown,\n", "")

    text = replace_function(
        text,
        "list_models",
        '''    def list_models(self) -> List[Dict[str, Any]]:
        data = self._req("GET", "/models")
        return data.get("data", [])''',
    )
    text = replace_function(
        text,
        "list_files",
        '''    def list_files(self) -> List[Dict[str, Any]]:
        return self._list_all("/files")''',
    )
    text = replace_function(
        text,
        "upload_file",
        '''    def upload_file(self, path: str, purpose: str = "user_data") -> Dict[str, Any]:
        if purpose == "batch":
            if os.path.getsize(path) > 200_000_000:
                raise ValueError("Dávka překračuje 200 MB.")
            with open(path, "rb") as stream:
                self.validate_batch_data(stream.read())
        with open(path, "rb") as stream:
            files = {"file": (os.path.basename(path), stream)}
            data = {"purpose": purpose}
            return self._req("POST", "/files", json_body=data, files=files)''',
    )
    text = replace_function(
        text,
        "delete_file",
        '''    def delete_file(self, file_id: str) -> Dict[str, Any]:
        self._validate_resource_id(file_id)
        return self._req("DELETE", f"/files/{file_id}")''',
    )
    text = replace_function(
        text,
        "file_content",
        '''    def file_content(self, file_id: str) -> bytes:
        self._validate_resource_id(file_id)
        return self._req("GET", f"/files/{file_id}/content")''',
    )
    text = replace_function(
        text,
        "retrieve_file",
        '''    def retrieve_file(self, file_id: str) -> Dict[str, Any]:
        self._validate_resource_id(file_id)
        return self._req("GET", f"/files/{file_id}")''',
    )
    text = replace_function(
        text,
        "_response_operation",
        '''    def _response_operation(self, response_id, *, cancel=False):
        self._validate_resource_id(response_id)
        if cancel:
            return self._req("POST", f"/responses/{response_id}/cancel", max_attempts=1)
        return self._req("GET", f"/responses/{response_id}")''',
    )
    text = replace_function(
        text,
        "_send_response",
        '''    def _send_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
            raise''',
    )
    if "self._sdk." in text:
        raise RuntimeError("Po migraci zůstala operace vedená přímo přes SDK.")
    path.write_text(text, encoding="utf-8")


def migrate_pipeline() -> None:
    path = ROOT / "kajovo/core/pipeline.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from .retry import CircuitBreaker, with_retry\n",
        "",
        "pipeline retry import",
    )
    text = replace_once(
        text,
        "        self.breaker = CircuitBreaker(settings.retry.circuit_breaker_failures, settings.retry.circuit_breaker_cooldown_s)\n",
        "",
        "pipeline breaker",
    )
    replacements = {
        "with_retry(lambda f=fid: client.retrieve_file(f), self.settings.retry, self.breaker)": "client.retrieve_file(fid)",
        "with_retry(lambda: client.create_vector_store(f\"IN_{ts_code()}\"), self.settings.retry, self.breaker)": "client.create_vector_store(f\"IN_{ts_code()}\")",
        "[with_retry(lambda f=fid: client.retrieve_file(f), self.settings.retry, self.breaker)\n                        for fid in file_ids + image_ids]": "[client.retrieve_file(fid) for fid in file_ids + image_ids]",
        "with_retry(lambda: client.create_vector_store(f\"DIAG_{ts_code()}\"), self.settings.retry, self.breaker)": "client.create_vector_store(f\"DIAG_{ts_code()}\")",
        "retrieve=lambda vector_store_id, file_id: with_retry(\n                lambda: client.retrieve_vector_store_file(vector_store_id, file_id),\n                self.settings.retry,\n                self.breaker,\n            ),": "retrieve=lambda vector_store_id, file_id: client.retrieve_vector_store_file(vector_store_id, file_id),",
        "with_retry(lambda: client.create_vector_store(f\"{(self.cfg.project or root_name)}{ts_code()}\"), self.settings.retry, self.breaker)": "client.create_vector_store(f\"{(self.cfg.project or root_name)}{ts_code()}\")",
    }
    for old, new in replacements.items():
        text = replace_once(text, old, new, f"pipeline {old[:32]}")
    if "with_retry" in text or "self.breaker" in text:
        raise RuntimeError("Po migraci zůstala v pipeline vnější retry vrstva.")
    path.write_text(text, encoding="utf-8")


def migrate_cascade() -> None:
    path = ROOT / "kajovo/core/cascade_pipeline.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from .retry import CircuitBreaker, with_retry\n",
        "",
        "cascade retry import",
    )
    text = replace_once(
        text,
        "        self.breaker = CircuitBreaker(\n            settings.retry.circuit_breaker_failures,\n            settings.retry.circuit_breaker_cooldown_s,\n        )\n",
        "",
        "cascade breaker",
    )
    if "with_retry" in text or "self.breaker" in text:
        raise RuntimeError("Po migraci zůstala v cascade vnější retry vrstva.")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    migrate_client()
    migrate_pipeline()
    migrate_cascade()
    print("Autoritativní OpenAI retry boundary byla aplikována.")


if __name__ == "__main__":
    main()
