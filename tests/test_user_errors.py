"""Překlad nikdy nesmí přisoudit obecné chybě nedoloženou příčinu."""

import errno
import smtplib

import pytest

from kajovo.core.openai_client import OpenAIError
from kajovo.core.contracts import ContractError
from kajovo.core.user_errors import describe_error, describe_recorded_error


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


def test_unknown_english_validation_error_is_not_shown_as_user_message():
    report = describe_error(ValueError("invalid parameter foo_bar"), operation="Uložení")
    assert "zadaným nebo uloženým údajům" in report.message
    assert "invalid parameter" not in report.message
    assert report.next_step.startswith("Zkontrolujte")


def test_legacy_user_launch_path_error_has_plain_czech_explanation():
    report = describe_error(
        ContractError("IFC-USER-LAUNCH: interface odkazuje na neznámou cestu.")
    )
    assert report.code == "interface.unknown_path"
    assert "chybně propojil jeho části" in report.message
    assert "vznikly soubory programu" in report.message
    assert "IFC-USER-LAUNCH" not in report.message


def test_recorded_provider_error_uses_human_catalog_and_redacts_secrets():
    report = describe_recorded_error(
        '{"error":{"code":"invalid_input_fidelity_model","message":"Bearer sk-proj-secretvalue"}}'
    )
    assert "volbu pro zachov\u00e1n\u00ed detail\u016f p\u016fvodn\u00ed fotografie" in report.message
    assert "sk-proj-secretvalue" not in report.detail
    assert "[SKRYTÝ KLÍČ]" in report.detail
