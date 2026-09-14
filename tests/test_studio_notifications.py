"""Dialog obdrží původní typ poštovní chyby a může vysvětlit ověřenou příčinu."""

import smtplib
from unittest.mock import Mock

import pytest

from kajovo.core.config import SMTPSettings
from kajovo.core.notifications import send_smtp_notification


def test_smtp_can_preserve_structured_failure(monkeypatch):
    error = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
    monkeypatch.setattr(smtplib, "SMTP", Mock(side_effect=error))
    settings = SMTPSettings(host="smtp.invalid", to_email="test@example.invalid", use_ssl=False)
    with pytest.raises(smtplib.SMTPAuthenticationError) as caught:
        send_smtp_notification(settings, "Test", "Test", raise_errors=True)
    assert caught.value is error
    assert send_smtp_notification(settings, "Test", "Test")[0] is False


def test_unconfigured_notification_is_not_reported_as_success():
    with pytest.raises(ValueError, match="příjemce"):
        send_smtp_notification(SMTPSettings(), "Test", "Test", raise_errors=True)
