from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Tuple

from .config import SMTPSettings


def send_smtp_notification(
    smtp: SMTPSettings, subject: str, body: str
) -> Tuple[bool, str]:
    """Odešle zprávu podle nastavení SMTP.

    Vrací dvojici (ok, message); při ok=False zpráva popisuje chybu."""
    host = (smtp.host or "").strip()
    to_email = (smtp.to_email or "").strip()
    if not host or not to_email:
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
            server.ehlo()
            if smtp.use_tls and not smtp.use_ssl:
                server.starttls(context=context)
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True, "Notification sent."
    except Exception as exc:  # pragma: no cover - záložní záznam chyby
        return False, f"SMTP send failed: {exc}"
