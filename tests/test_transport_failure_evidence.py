"""Neodeslaný požadavek a neurčité přijetí mají odlišnou evidenci."""
import pytest
import requests

from kajovo.core.config import RetryPolicy
from kajovo.core.openai_transport import (
    CREATE_RESPONSE, LIST_MODELS, OpenAIError, OpenAITransport, SubmissionOutcomeUnknown,
)
from kajovo.core.retry import CircuitBreaker, with_retry


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


class Response:
    def __init__(self, status, content, *, content_type="application/json"):
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.headers = {"content-type": content_type, "x-request-id": "req_evidence"}

    def json(self):
        import json
        return json.loads(self.content)


def client(session):
    return OpenAITransport(base_url="https://example.invalid/v1", api_key="test",
                           timeout_s=2, session=session, sleeper=lambda _: None)


@pytest.mark.parametrize("body", [[], {"bad": float("nan")}, {"bad": object()}])
def test_invalid_local_body_never_dispatches(body):
    session = Session(None)
    with pytest.raises(OpenAIError) as caught:
        client(session).request(CREATE_RESPONSE, "POST", "/responses", json_body=body)
    assert caught.value.request_sent is False
    assert not session.calls


def test_failed_dispatch_evidence_never_sends_request():
    session = Session(None)
    def observer(event):
        assert event == "dispatch_started"
        raise OSError("evidence disk failed")
    with pytest.raises(OpenAIError) as caught:
        client(session).request(CREATE_RESPONSE, "POST", "/responses", json_body={}, observer=observer)
    assert caught.value.code == "local_request_evidence_failed"
    assert caught.value.request_sent is False
    assert not session.calls


@pytest.mark.parametrize("content", [b'{"id":', b'{"id":"first","id":"second"}', b"\xff", b"[]"])
def test_malformed_read_is_normalized_without_losing_request_id(content):
    session = Session(Response(200, content))
    with pytest.raises(OpenAIError) as caught:
        client(session).request(LIST_MODELS, "GET", "/models")
    assert not isinstance(caught.value, SubmissionOutcomeUnknown)
    assert caught.value.request_id == "req_evidence"
    assert len(session.calls) == 1


def test_non_json_submit_is_unknown_and_never_retried():
    session = Session(Response(200, b"<html>gateway</html>", content_type="text/html"))
    with pytest.raises(SubmissionOutcomeUnknown) as caught:
        client(session).request(CREATE_RESPONSE, "POST", "/responses", json_body={})
    assert caught.value.request_id == "req_evidence"
    assert len(session.calls) == 1
    method, url, args = session.calls[0]
    assert (method, url, args["json"]) == ("POST", "https://example.invalid/v1/responses", {})


@pytest.mark.parametrize("content", [b"not json", b"[]", b'{"error":"invalid shape"}'])
def test_http_error_body_cannot_hide_http_status(content):
    session = Session(Response(403, content))
    with pytest.raises(OpenAIError) as caught:
        client(session).request(CREATE_RESPONSE, "POST", "/responses")
    assert caught.value.status_code == 403
    assert caught.value.request_id == "req_evidence"
    assert caught.value.code is None
    assert len(session.calls) == 1


def test_retry_breaker_wait_and_exhaustion_preserve_last_error(monkeypatch):
    from kajovo.core import retry
    sleeps = []
    monkeypatch.setattr(retry.time, "monotonic", lambda: 10)
    monkeypatch.setattr(retry.time, "sleep", sleeps.append)
    policy = RetryPolicy(max_attempts=2, base_delay_s=1, max_delay_s=2, jitter_s=0)
    breaker = CircuitBreaker(failures=1, cooldown_s=5)
    attempts = []
    error = OSError("unavailable local input")
    def fail():
        attempts.append(1)
        raise error
    with pytest.raises(OSError) as caught:
        with_retry(fail, policy, breaker)
    assert caught.value is error
    assert len(attempts) == 2
    assert sleeps == [1, 5]
    assert not breaker.allow()
    breaker.on_success()
    assert breaker.allow()


def test_invalid_retry_attempt_count_rejected_before_function():
    calls = []
    with pytest.raises(ValueError):
        with_retry(lambda: calls.append(1), RetryPolicy(max_attempts=0))
    assert not calls


def test_safe_read_generic_network_failure_has_one_normalized_error():
    class Broken(Session):
        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise requests.RequestException("invalid redirect")
    session = Broken(None)
    with pytest.raises(OpenAIError) as caught:
        client(session).request(LIST_MODELS, "GET", "/models")
    assert not isinstance(caught.value, SubmissionOutcomeUnknown)
    assert len(session.calls) == 1
