"""Plain-text email via Gmail SMTP (TLS port 587).

Reads GMAIL_FROM_ADDRESS and GMAIL_APP_PASSWORD from the environment. Both
must be set; if either is missing, sends silently degrade to a logged warning
so registration never fails because of an SMTP outage.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger("uvicorn.error")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 10


def is_configured() -> bool:
    return bool(
        os.environ.get("GMAIL_APP_PASSWORD", "").strip()
        and os.environ.get("GMAIL_FROM_ADDRESS", "").strip()
    )


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True on success, False on any failure."""
    if not is_configured():
        logger.warning(
            "GMAIL_APP_PASSWORD or GMAIL_FROM_ADDRESS not set; skipping email to %s",
            to_address,
        )
        return False

    from_address = os.environ["GMAIL_FROM_ADDRESS"].strip()
    password = os.environ["GMAIL_APP_PASSWORD"]

    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(from_address, password)
            smtp.send_message(msg)
    except Exception as exc:
        logger.warning("Failed to send email to %s: %s", to_address, exc)
        return False
    logger.info("Sent email to %s subject=%r", to_address, subject)
    return True


WELCOME_SUBJECT = "Quiz Patente B — il tuo token di accesso"

WELCOME_BODY = """Benvenuto su Quiz Patente B!

Il tuo token personale per accedere all'app è:

    {token}

Conservalo in un posto sicuro: serve ogni volta che ti colleghi da un nuovo
dispositivo o cancelli i dati del browser. Non lo condividere con nessuno.

Se perdi il token puoi richiederne uno nuovo dalla pagina di accesso (link
"Token dimenticato"); il nuovo token sostituirà quello attuale.

Buono studio!
"""


FORGOT_SUBJECT = "Quiz Patente B — il tuo nuovo token"

FORGOT_BODY = """Hai richiesto un nuovo token per Quiz Patente B.

Il tuo nuovo token è:

    {token}

Il vecchio token non è più valido. Sostituiscilo nell'app: pagina di accesso
→ "Ho già un token" → inserisci email e nuovo token.

Se non hai richiesto questo cambio, ignora questo messaggio: il vecchio token
è già stato invalidato e nessuno può usarlo. Puoi richiedere un altro nuovo
token quando vuoi.
"""


def send_welcome_token(email: str, token: str) -> bool:
    return send_email(email, WELCOME_SUBJECT, WELCOME_BODY.format(token=token))


def send_forgot_token(email: str, token: str) -> bool:
    return send_email(email, FORGOT_SUBJECT, FORGOT_BODY.format(token=token))
