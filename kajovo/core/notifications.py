from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Tuple

from .config import SMTPSettings


def _validate_smtp_reply(reply, codes, operation):
    """SMTP reply je dvojice stavového kódu a skutečných protokolových bytes."""
    if (not isinstance(reply, tuple) or len(reply) != 2
            or type(reply[0]) is not int or not isinstance(reply[1], bytes)):
        raise ValueError(f"{operation}: neplatný kontrakt SMTP odpovědi.")
    if reply[0] not in codes:
        raise smtplib.SMTPResponseException(*reply)


def send_smtp_notification(
    smtp: SMTPSettings, subject: str, body: str, *, raise_errors: bool = False
) -> Tuple[bool, str]:
    """Odešle zprávu podle nastavení SMTP.

    Vrací dvojici (ok, message); při ok=False zpráva popisuje chybu."""
    host = (smtp.host or "").strip()
    to_email = (smtp.to_email or "").strip()
    if not host or not to_email:
        if raise_errors:
            raise ValueError("Vyplňte poštovní server a příjemce oznámení.")
        return False, "SMTP server or recipient is not configured."

    try:
        port = int(smtp.port or 0) or 587
        username = (smtp.username or "").strip()
        password = smtp.password or ""
        from_email = (smtp.from_email or username or to_email).strip()

        msg = EmailMessage()
        msg["From"] = from_email or "kajovo@localhost"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        context = ssl.create_default_context()
        if smtp.use_ssl:
            client = smtplib.SMTP_SSL(host=host, port=port, timeout=20, context=context)
        else:
            client = smtplib.SMTP(host=host, port=port, timeout=20)
        with client as server:
            _validate_smtp_reply(server.ehlo(), {250}, "EHLO")
            if smtp.use_tls and not smtp.use_ssl:
                _validate_smtp_reply(server.starttls(context=context), {220}, "STARTTLS")
                _validate_smtp_reply(server.ehlo(), {250}, "EHLO po TLS")
            if username:
                _validate_smtp_reply(server.login(username, password), {235, 503}, "AUTH")
            refused = server.send_message(msg)
            recipients = {address for _, address in getaddresses([to_email])}
            if not isinstance(refused, dict) or set(refused) - recipients:
                raise ValueError("SMTP vrátilo nepopsané odmítnuté příjemce.")
            for reply in refused.values():
                _validate_smtp_reply(reply, set(range(400, 600)), "RCPT odmítnutí")
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        return True, "Notification sent."
    except Exception as exc:  # pragma: no cover - záložní záznam chyby
        if raise_errors:
            raise
        return False, f"SMTP send failed: {exc}"
