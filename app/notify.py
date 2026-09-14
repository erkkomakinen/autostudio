"""Ylläpidon ilmoitukset sähköpostilla.

Ympäristömuuttujat (ilman niitä ilmoitus näkyy vain ylläpidon Palautteet-välilehdellä):
AUTOSTUDIO_SMTP_HOST, AUTOSTUDIO_SMTP_PORT (587 STARTTLS / 465 SSL), AUTOSTUDIO_SMTP_USER,
AUTOSTUDIO_SMTP_PASSWORD, AUTOSTUDIO_SMTP_FROM, AUTOSTUDIO_ADMIN_EMAIL
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("autostudio.notify")


def configured() -> bool:
    return bool(os.environ.get("AUTOSTUDIO_SMTP_HOST") and os.environ.get("AUTOSTUDIO_ADMIN_EMAIL"))


def send(subject: str, body: str, attachments: list[tuple[Path, str]]) -> bool:
    if not configured():
        log.info("Sähköposti-ilmoitus ohitettu (SMTP ei asetettu): %s", subject)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("AUTOSTUDIO_SMTP_FROM") or os.environ["AUTOSTUDIO_ADMIN_EMAIL"]
    msg["To"] = os.environ["AUTOSTUDIO_ADMIN_EMAIL"]
    msg.set_content(body)
    for path, filename in attachments:
        if path.exists():
            msg.add_attachment(path.read_bytes(), maintype="image", subtype="jpeg", filename=filename)
    host = os.environ["AUTOSTUDIO_SMTP_HOST"]
    port = int(os.environ.get("AUTOSTUDIO_SMTP_PORT", "587"))
    user, password = os.environ.get("AUTOSTUDIO_SMTP_USER"), os.environ.get("AUTOSTUDIO_SMTP_PASSWORD")
    try:
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as s:
                if user:
                    s.login(user, password or "")
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=context)
                if user:
                    s.login(user, password or "")
                s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001 - ilmoituksen epäonnistuminen ei saa kaataa käsittelyä
        log.error("Sähköposti-ilmoitus epäonnistui: %s", e)
        return False
