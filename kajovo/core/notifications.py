from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Tuple

from .config import SMTPSettings


def send_smtp_notification(
    smtp: SMTPSettings, subject: str, body: str
) -> Tuple[bool, str]:
    """
    Send a simple email using the provided SMTP settings.

    Returns (ok, message) where ok=False contains the error description.
    """
    host = (smtp.host or "").strip()
    to_email = (smtp.to_email or "").strip()
    if not host or not to_email:
        return False, "SMTP server or recipient is not configured."

    port = int(smtp.port or 0) or 587
    username = (smtp.username or "").strip()
    password = smtp.password or ""
    from_email = (smtp.from_email or username or to_email).strip()

    msg = EmailMessage()
    msg["From"] = from_email or "kajovo@localhost"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if smtp.use_ssl:
            client = smtplib.SMTP_SSL(host=host, port=port, timeout=20)
        else:
            client = smtplib.SMTP(host=host, port=port, timeout=20)
        with client as server:
            server.ehlo()
            if smtp.use_tls and not smtp.use_ssl:
                server.starttls()
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True, "Notification sent."
    except Exception as exc:  # pragma: no cover - defensive logging
        return False, f"SMTP send failed: {exc}"
