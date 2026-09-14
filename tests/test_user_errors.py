"""Překlad nikdy nesmí přisoudit obecné chybě nedoloženou příčinu."""

import errno
import smtplib

import pytest

from kajovo.core.openai_client import OpenAIError
from kajovo.core.user_errors import describe_error


@pytest.mark.parametrize("code", ["insufficient_quota", "rate_limit_exceeded"])
def test_specific_provider_code_survives_wrapping(code):
    root = OpenAIError("provider detail", status_code=429, code=code)
    wrapped = RuntimeError("operation failed")
    wrapped.__cause__ = root
    report = describe_error(wrapped)
    assert report.code == code
    assert report.cause_known
    assert "provider detail" in report.detail
    assert not report.retry_safe


def test_http_429_does_not_guess_credit():
    report = describe_error(OpenAIError("unknown", status_code=429))
    assert not report.cause_known
    assert "kredit" not in report.message


def test_timeout_does_not_claim_remote_operation_failed():
    report = describe_error(TimeoutError())
    assert "výsledek zatím není znám" in report.message
    assert not report.retry_safe


def test_filesystem_preserves_proven_cause():
    report = describe_error(OSError(errno.ENOSPC, "disk full"))
    assert report.cause_known
    assert "volného místa" in report.message


def test_smtp_authentication_is_not_classified_as_filesystem_error():
    report = describe_error(smtplib.SMTPAuthenticationError(535, b"authentication failed"))
    assert report.domain == "smtp"
    assert "přihlášení" in report.message


def test_arbitrary_message_cannot_fake_a_known_cause():
    report = describe_error(RuntimeError("invalid_api_key insufficient_quota"))
    assert report.domain == "unknown"
    assert not report.cause_known
