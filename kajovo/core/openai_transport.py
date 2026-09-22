"""Autoritativni transportni a retry politika pro OpenAI API.

Modul zamerne nerozhoduje podle samotne HTTP metody. Kazda podporovana
operace ma explicitni :class:`OperationSpec`; nezname operace jsou
konzervativne jednopokusove.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

import requests

from .orchestration.contracts import canonical_bytes, parse_json_strict
from .orchestration.errors import OrchestrationError


class OpenAIError(Exception):
    """Normalizovana chyba poskytovatele nebo transportu."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        param: object = None,
        code: object = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.param = param
        self.code = code
        self.request_id: str | None = None
        self.request_sent: bool | None = None


class OperationEffect(Enum):
    SAFE_READ = "safe_read"
    IDEMPOTENT = "idempotent"
    RETRY_SAFE = "retry_safe"
    NON_IDEMPOTENT_SIDE_EFFECT = "non_idempotent_side_effect"


class SubmissionOutcome(Enum):
    NOT_SENT = "not_sent"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OperationSpec:
    name: str
    effect: OperationEffect
    max_attempts: int
    retry_http_statuses: frozenset[int]
    retry_transport_errors: bool

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts musi byt alespon 1")
        if (
            self.effect is OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT
            and self.max_attempts != 1
        ):
            raise ValueError("Neidempotentni side effect smi mit prave jeden pokus")


class SubmissionOutcomeUnknown(OpenAIError):
    """Request mohl byt proveden, ale klient nema potvrzeny vysledek."""

    def __init__(
        self,
        operation: str,
        method: str,
        path: str,
        request_id: str | None = None,
        *,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            f"{operation}: vysledek odeslani neni znam ({method} {path})"
        )
        self.operation = operation
        self.method = method
        self.path = path
        self.request_id = request_id
        self.outcome = SubmissionOutcome.UNKNOWN
        if cause is not None:
            self.__cause__ = cause


_TRANSIENT_HTTP = frozenset({429, *range(500, 600)})


def _safe_read(name: str) -> OperationSpec:
    return OperationSpec(
        name=name,
        effect=OperationEffect.SAFE_READ,
        max_attempts=4,
        retry_http_statuses=_TRANSIENT_HTTP,
        retry_transport_errors=True,
    )


def _single(name: str, effect: OperationEffect) -> OperationSpec:
    return OperationSpec(
        name=name,
        effect=effect,
        max_attempts=1,
        retry_http_statuses=frozenset(),
        retry_transport_errors=False,
    )


LIST_MODELS = _safe_read("list_models")
LIST_FILES = _safe_read("list_files")
RETRIEVE_FILE = _safe_read("retrieve_file")
FILE_CONTENT = _safe_read("file_content")
RETRIEVE_RESPONSE = _safe_read("retrieve_response")
LIST_VECTOR_STORES = _safe_read("list_vector_stores")
RETRIEVE_VECTOR_STORE = _safe_read("retrieve_vector_store")
LIST_VECTOR_STORE_FILES = _safe_read("list_vector_store_files")
RETRIEVE_VECTOR_STORE_FILE = _safe_read("retrieve_vector_store_file")
LIST_BATCHES = _safe_read("list_batches")
RETRIEVE_BATCH = _safe_read("retrieve_batch")

CREATE_RESPONSE = _single(
    "create_response", OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT
)
CREATE_BATCH = _single("create_batch", OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT)
UPLOAD_FILE = _single("upload_file", OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT)
CREATE_VECTOR_STORE = _single(
    "create_vector_store", OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT
)
ATTACH_VECTOR_STORE_FILE = _single(
    "attach_vector_store_file", OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT
)
CREATE_IMAGE = _single("create_image", OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT)
CREATE_IMAGE_BATCH = _single(
    "create_image_batch", OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT
)

CANCEL_RESPONSE = _single("cancel_response", OperationEffect.IDEMPOTENT)
CANCEL_BATCH = _single("cancel_batch", OperationEffect.IDEMPOTENT)
DELETE_FILE = _single("delete_file", OperationEffect.IDEMPOTENT)
DELETE_VECTOR_STORE = _single("delete_vector_store", OperationEffect.IDEMPOTENT)
DELETE_VECTOR_STORE_FILE = _single(
    "delete_vector_store_file", OperationEffect.IDEMPOTENT
)
UPDATE_VECTOR_STORE_FILE = _single(
    "update_vector_store_file", OperationEffect.IDEMPOTENT
)
INPUT_TOKEN_COUNT = _single("input_token_count", OperationEffect.IDEMPOTENT)
UNKNOWN_OPERATION = _single("unknown_operation", OperationEffect.IDEMPOTENT)


def operation_spec(method: str, path: str) -> OperationSpec:
    """Vrati explicitni policy pro podporovany endpoint.

    Funkce je kompatibilitni hranice pro stavajiciho klienta. Rozhodnuti je
    centralizovane zde; samotne ``POST``/``GET`` nikdy nestaci k povoleni retry.
    """

    method = method.upper()
    clean = path.split("?", 1)[0]

    exact: dict[tuple[str, str], OperationSpec] = {
        ("GET", "/models"): LIST_MODELS,
        ("GET", "/files"): LIST_FILES,
        ("POST", "/files"): UPLOAD_FILE,
        ("POST", "/responses"): CREATE_RESPONSE,
        ("POST", "/responses/input_tokens"): INPUT_TOKEN_COUNT,
        ("POST", "/batches"): CREATE_BATCH,
        ("GET", "/batches"): LIST_BATCHES,
        ("POST", "/vector_stores"): CREATE_VECTOR_STORE,
        ("GET", "/vector_stores"): LIST_VECTOR_STORES,
        ("POST", "/images/generations"): CREATE_IMAGE,
        ("POST", "/images/edits"): CREATE_IMAGE,
    }
    if (method, clean) in exact:
        return exact[(method, clean)]

    patterns: tuple[tuple[str, str, OperationSpec], ...] = (
        ("GET", r"/files/[A-Za-z0-9_-]+/content", FILE_CONTENT),
        ("GET", r"/files/[A-Za-z0-9_-]+", RETRIEVE_FILE),
        ("DELETE", r"/files/[A-Za-z0-9_-]+", DELETE_FILE),
        ("GET", r"/responses/[A-Za-z0-9_-]+", RETRIEVE_RESPONSE),
        ("POST", r"/responses/[A-Za-z0-9_-]+/cancel", CANCEL_RESPONSE),
        ("GET", r"/batches/[A-Za-z0-9_-]+", RETRIEVE_BATCH),
        ("POST", r"/batches/[A-Za-z0-9_-]+/cancel", CANCEL_BATCH),
        ("GET", r"/vector_stores/[A-Za-z0-9_-]+", RETRIEVE_VECTOR_STORE),
        ("DELETE", r"/vector_stores/[A-Za-z0-9_-]+", DELETE_VECTOR_STORE),
        (
            "GET",
            r"/vector_stores/[A-Za-z0-9_-]+/files",
            LIST_VECTOR_STORE_FILES,
        ),
        (
            "POST",
            r"/vector_stores/[A-Za-z0-9_-]+/files",
            ATTACH_VECTOR_STORE_FILE,
        ),
        (
            "GET",
            r"/vector_stores/[A-Za-z0-9_-]+/files/[A-Za-z0-9_-]+",
            RETRIEVE_VECTOR_STORE_FILE,
        ),
        (
            "DELETE",
            r"/vector_stores/[A-Za-z0-9_-]+/files/[A-Za-z0-9_-]+",
            DELETE_VECTOR_STORE_FILE,
        ),
        (
            "POST",
            r"/vector_stores/[A-Za-z0-9_-]+/files/[A-Za-z0-9_-]+",
            UPDATE_VECTOR_STORE_FILE,
        ),
    )
    for expected_method, pattern, spec in patterns:
        if method == expected_method and re.fullmatch(pattern, clean):
            return spec
    return replace(UNKNOWN_OPERATION, name=f"{method} {clean}")


class OpenAITransport:
    """Jediny vlastnik REST retry rozhodnuti."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter_source: Callable[[], float] = random.random,
        backoff_base_s: float = 0.8,
        backoff_cap_s: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = float(timeout_s)
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self.clock = clock
        self.jitter_source = jitter_source
        self.backoff_base_s = float(backoff_base_s)
        self.backoff_cap_s = float(backoff_cap_s)

    @staticmethod
    def _safe_excerpt(text: str, max_chars: int = 1200) -> str:
        return (text or "")[:max_chars]

    def _delay(self, attempt: int, retry_after: str) -> float:
        try:
            if retry_after:
                return max(0.0, min(float(retry_after), self.backoff_cap_s))
        except (TypeError, ValueError):
            pass
        base = min(self.backoff_cap_s, self.backoff_base_s * 2 ** max(0, attempt - 1))
        jitter = max(0.0, min(float(self.jitter_source()), 1.0))
        return min(self.backoff_cap_s, base * (1.0 + 0.1 * jitter))

    def request(
        self,
        spec: OperationSpec,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> Any:
        if files is None and json_body is not None:
            try:
                if not isinstance(json_body, dict):
                    raise ValueError("Tělo požadavku API musí být JSON objekt.")
                canonical_bytes(json_body)
            except (ValueError, TypeError) as exc:
                error = OpenAIError(f"{method} {path}: neplatné JSON tělo požadavku: {exc}")
                error.request_sent = False
                raise error from exc

        attempts = spec.max_attempts
        if max_attempts is not None:
            attempts = min(attempts, max(1, int(max_attempts)))
        if spec.effect is OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT:
            attempts = 1

        url = self.base_url + path
        req_timeout = float(timeout if timeout is not None else self.timeout_s)
        file_positions: list[tuple[Any, int]] = []
        for value in (files or {}).values():
            stream = value[1] if isinstance(value, tuple) else value
            if hasattr(stream, "tell") and hasattr(stream, "seek"):
                file_positions.append((stream, stream.tell()))

        for attempt in range(1, attempts + 1):
            for stream, position in file_positions:
                stream.seek(position)
            try:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                if files is None:
                    headers["Content-Type"] = "application/json"
                    response = self.session.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        timeout=req_timeout,
                    )
                else:
                    response = self.session.request(
                        method,
                        url,
                        headers=headers,
                        data=json_body,
                        files=files,
                        timeout=req_timeout,
                    )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if spec.effect is OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT:
                    raise SubmissionOutcomeUnknown(
                        spec.name, method, path, cause=exc
                    ) from exc
                if spec.retry_transport_errors and attempt < attempts:
                    self.sleeper(self._delay(attempt, ""))
                    continue
                raise OpenAIError(f"{method} {path} failed: {exc}") from exc
            except requests.RequestException as exc:
                if spec.effect is OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT:
                    raise SubmissionOutcomeUnknown(spec.name, method, path, cause=exc) from exc
                raise OpenAIError(f"{method} {path} failed: {exc}") from exc

            request_id = response.headers.get("x-request-id")
            if response.status_code >= 400:
                excerpt = self._safe_excerpt(getattr(response, "text", ""))
                transient = response.status_code in spec.retry_http_statuses
                if transient and attempt < attempts:
                    self.sleeper(
                        self._delay(attempt, str(response.headers.get("retry-after", "")))
                    )
                    continue
                try:
                    detail = response.json().get("error", {})
                    detail = detail if isinstance(detail, dict) else {}
                except (ValueError, AttributeError):
                    detail = {}
                if (
                    spec.effect is OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT
                    and 500 <= response.status_code < 600
                ):
                    raise SubmissionOutcomeUnknown(
                        spec.name, method, path, request_id=request_id
                    )
                error = OpenAIError(
                    f"{method} {path} -> {response.status_code}: {excerpt}",
                    status_code=response.status_code,
                    param=detail.get("param"),
                    code=detail.get("code"),
                )
                error.request_id = request_id
                raise error

            if spec.name == FILE_CONTENT.name:
                return response.content

            content_type = str(response.headers.get("content-type", "")).lower()
            raw_content = getattr(response, "content", None)
            if not content_type.startswith("application/json"):
                return raw_content if isinstance(raw_content, bytes) else response.content

            try:
                if isinstance(raw_content, (bytes, bytearray)):
                    result = parse_json_strict(
                        bytes(raw_content).decode("utf-8", errors="strict")
                    )
                else:
                    result = response.json()
                    if not isinstance(result, dict):
                        raise OrchestrationError(
                            "ROOT_NOT_OBJECT", "Kořen JSON musí být objekt."
                        )
                    canonical_bytes(result)
            except (UnicodeError, ValueError, OrchestrationError) as exc:
                if spec.effect is OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT:
                    raise SubmissionOutcomeUnknown(
                        spec.name, method, path, request_id=request_id, cause=exc
                    ) from exc
                error = OpenAIError(f"{method} {path}: neplatná JSON odpověď")
                error.request_id = request_id
                raise error from exc
            if request_id:
                result["_request_id"] = request_id
            return result

        raise AssertionError("Nedostupny stav transportni smycky")
