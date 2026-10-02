"""Částečné odmítnutí ani vadná protokolová odpověď nepotvrzuje SMTP úspěch."""
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from kajovo.core.config import SMTPSettings
from kajovo.core.notifications import send_smtp_notification


def mail_client():
    client = MagicMock()
    server = client.__enter__.return_value
    server.ehlo.return_value = (250, b"OK")
    server.starttls.return_value = (220, b"TLS ready")
    server.login.return_value = (235, b"Authenticated")
    server.send_message.return_value = {}
    return client, server


@pytest.mark.parametrize("operation,bad", [
    ("ehlo", None), ("ehlo", (250, "text")), ("ehlo", (True, b"OK")),
    ("ehlo", (500, b"Failed")), ("starttls", (250, b"Wrong phase")),
    ("login", (535, b"Denied")), ("send_message", None),
    ("send_message", {"foreign@example.test": (550, b"Denied")}),
    ("send_message", {"user@example.test": (550, "text")}),
    ("send_message", {"user@example.test": (250, b"Accepted is not refusal")}),
])
def test_smtp_contract_rejects_invalid_protocol_and_unknown_recipient(operation, bad):
    client, server = mail_client()
    getattr(server, operation).return_value = bad
    settings = SMTPSettings(host="smtp.example.test", to_email="user@example.test", username="user")
    with patch("kajovo.core.notifications.smtplib.SMTP", return_value=client):
        assert send_smtp_notification(settings, "Test", "Obsah")[0] is False


def test_partial_recipient_refusal_is_failure_and_preserves_identity():
    client, server = mail_client()
    refused = {"second@example.test": (550, b"Denied")}
    server.send_message.return_value = refused
    settings = SMTPSettings(host="smtp.example.test", to_email="first@example.test, second@example.test")
    with patch("kajovo.core.notifications.smtplib.SMTP", return_value=client):
        with pytest.raises(smtplib.SMTPRecipientsRefused) as caught:
            send_smtp_notification(settings, "Test", "Obsah", raise_errors=True)
        assert caught.value.recipients == refused
        assert send_smtp_notification(settings, "Test", "Obsah")[0] is False


def test_empty_refusal_map_is_the_exact_success_contract():
    client, _ = mail_client()
    with patch("kajovo.core.notifications.smtplib.SMTP", return_value=client):
        assert send_smtp_notification(SMTPSettings(host="smtp.example.test", to_email="user@example.test"), "Test", "Obsah")[0] is True
