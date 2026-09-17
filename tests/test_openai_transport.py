from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from kajovo.core.openai_transport import (
    ATTACH_VECTOR_STORE_FILE,
    CREATE_RESPONSE,
    CREATE_VECTOR_STORE,
    LIST_MODELS,
    UPLOAD_FILE,
    OpenAIError,
    OpenAITransport,
    OperationEffect,
    OperationSpec,
    SubmissionOutcomeUnknown,
    operation_spec,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, *, headers=None, text=""):
        self.status_code = status
        self._payload = {} if payload is None else payload
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def transport(session):
    return OpenAITransport(
        base_url="https://example.invalid/v1",
        api_key="test",
        timeout_s=2,
        session=session,
        sleeper=lambda _seconds: None,
        jitter_source=lambda: 0.0,
    )


def test_net_001_safe_get_timeout_then_success_retries_once():
    session = FakeSession(requests.Timeout("lost"), FakeResponse(payload={"data": []}))
    result = transport(session).request(LIST_MODELS, "GET", "/models")
    assert result == {"data": []}
    assert len(session.calls) == 2


def test_net_002_safe_get_429_then_success_retries_once():
    session = FakeSession(FakeResponse(429), FakeResponse(payload={"data": []}))
    transport(session).request(LIST_MODELS, "GET", "/models")
    assert len(session.calls) == 2


def test_net_003_safe_get_500_then_success_retries_once():
    session = FakeSession(FakeResponse(500), FakeResponse(payload={"data": []}))
    transport(session).request(LIST_MODELS, "GET", "/models")
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "failure",
    [requests.Timeout("timeout"), requests.ConnectionError("reset")],
)
def test_net_004_005_response_transport_failure_is_single_unknown_submit(failure):
    session = FakeSession(failure)
    with pytest.raises(SubmissionOutcomeUnknown) as caught:
        transport(session).request(CREATE_RESPONSE, "POST", "/responses", json_body={})
    assert caught.value.operation == "create_response"
    assert caught.value.outcome.value == "unknown"
    assert len(session.calls) == 1


def test_net_006_response_429_is_not_retried():
    session = FakeSession(FakeResponse(429, {"error": {"code": "rate_limit"}}))
    with pytest.raises(OpenAIError) as caught:
        transport(session).request(CREATE_RESPONSE, "POST", "/responses", json_body={})
    assert caught.value.status_code == 429
    assert len(session.calls) == 1


def test_net_007_response_500_is_single_unknown_submit():
    session = FakeSession(FakeResponse(500, headers={"x-request-id": "req_1"}))
    with pytest.raises(SubmissionOutcomeUnknown) as caught:
        transport(session).request(CREATE_RESPONSE, "POST", "/responses", json_body={})
    assert caught.value.request_id == "req_1"
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("spec", "path"),
    [
        (UPLOAD_FILE, "/files"),
        (CREATE_VECTOR_STORE, "/vector_stores"),
        (ATTACH_VECTOR_STORE_FILE, "/vector_stores/vs_1/files"),
    ],
)
def test_net_008_009_010_side_effect_timeout_is_never_retried(spec, path):
    session = FakeSession(requests.Timeout("lost"))
    with pytest.raises(SubmissionOutcomeUnknown):
        transport(session).request(spec, "POST", path, json_body={})
    assert len(session.calls) == 1


def test_net_011_explicit_retry_safe_operation_uses_policy_attempt_count():
    spec = OperationSpec(
        name="safe_compute",
        effect=OperationEffect.RETRY_SAFE,
        max_attempts=3,
        retry_http_statuses=frozenset({503}),
        retry_transport_errors=True,
    )
    session = FakeSession(FakeResponse(503), FakeResponse(payload={"ok": True}))
    assert transport(session).request(spec, "POST", "/safe-compute", json_body={}) == {
        "ok": True
    }
    assert len(session.calls) == 2


def test_net_012_sdk_auto_retry_is_explicitly_disabled():
    with patch("openai.OpenAI") as sdk:
        from kajovo.core.openai_client import OpenAIClient

        OpenAIClient("test")
    assert sdk.call_args.kwargs["max_retries"] == 0


def test_transport_never_allows_multiple_attempts_for_non_idempotent_spec():
    with pytest.raises(ValueError):
        OperationSpec(
            name="bad",
            effect=OperationEffect.NON_IDEMPOTENT_SIDE_EFFECT,
            max_attempts=2,
            retry_http_statuses=frozenset({500}),
            retry_transport_errors=True,
        )


@pytest.mark.parametrize("path", ["/responses", "/batches", "/files", "/vector_stores",
                                   "/vector_stores/vs_test/files", "/images/edits", "/images/generations"])
@pytest.mark.parametrize("status", [429, 500, 503])
def test_all_side_effect_endpoints_send_once(path, status):
    session = FakeSession(FakeResponse(status, {"error": {"code": "temporary"}}))
    spec = operation_spec("POST", path)
    with pytest.raises(OpenAIError):
        transport(session).request(spec, "POST", path)
    assert len(session.calls) == 1


@pytest.mark.parametrize("method,path", [
    ("GET", "/files/f_1/content"), ("GET", "/responses/r_1"),
    ("GET", "/vector_stores/vs_1/files/f_1"), ("GET", "/batches/b_1"),
    ("DELETE", "/files/f_1"), ("POST", "/responses/r_1/cancel"),
    ("POST", "/vector_stores/vs_1/files/f_1"), ("POST", "/unregistered"),
])
def test_endpoint_policy_resolution(method, path):
    spec = operation_spec(method, path)
    assert (spec.max_attempts > 1) == (method == "GET")


def test_malformed_success_is_unknown_not_retryable():
    response = FakeResponse()
    response.json = lambda: (_ for _ in ()).throw(ValueError("broken JSON"))
    session = FakeSession(response)
    with pytest.raises(SubmissionOutcomeUnknown):
        transport(session).request(CREATE_RESPONSE, "POST", "/responses")
    assert len(session.calls) == 1


def test_retry_after_invalid_falls_back_and_safe_read_error_is_normalized():
    session = FakeSession(requests.RequestException("read error"))
    with pytest.raises(OpenAIError):
        transport(session).request(LIST_MODELS, "GET", "/models")
    assert transport(session)._delay(1, "invalid") == 0.8


def test_binary_content_response():
    response = FakeResponse(headers={"content-type": "application/octet-stream"})
    response.content = b"file bytes"
    session = FakeSession(response)
    assert transport(session).request(LIST_MODELS, "GET", "/models") == b"file bytes"
