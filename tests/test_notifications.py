import ssl
from unittest.mock import MagicMock, patch

import pytest

from kajovo.core.config import SMTPSettings
from kajovo.core.notifications import send_smtp_notification


@pytest.mark.parametrize("use_ssl", [False, True])
def test_smtp_verifies_server_certificate(use_ssl):
    settings = SMTPSettings(host="smtp.example.test", to_email="user@example.test", use_ssl=use_ssl)
    client = MagicMock()
    with patch("kajovo.core.notifications.smtplib.SMTP", return_value=client), patch(
        "kajovo.core.notifications.smtplib.SMTP_SSL", return_value=client
    ) as ssl_factory:
        assert send_smtp_notification(settings, "test", "body")[0]
    context = (ssl_factory.call_args.kwargs["context"] if use_ssl else
               client.__enter__.return_value.starttls.call_args.kwargs["context"])
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_invalid_mail_header_returns_failure_without_connecting():
    settings = SMTPSettings(host="smtp.example.test", to_email="user@example.test")
    with patch("kajovo.core.notifications.smtplib.SMTP") as connect:
        assert not send_smtp_notification(settings, "bad\nheader", "body")[0]
    connect.assert_not_called()
