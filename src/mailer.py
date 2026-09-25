"""Outgoing email, and nothing else.

Plain smtplib: no new dependency, and every mail host this deployment is
likely to meet (Microsoft 365, an internal relay, Gmail) speaks it. Settings
come from keystore at call time, so saving them in the console applies to
the very next message without a restart.

Deliberately synchronous and raising: callers that must not block or fail a
request (the low-credit alert fired from inside an answer) run it on a thread
and log the error; the console's "send test email" wants the error text so
an operator can see why their settings do not work.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import keystore

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20


class MailError(Exception):
    """Mail is not configured, or the server refused it. The message is
    safe to show a root operator: it never includes the password."""


def is_configured() -> bool:
    s = keystore.get_smtp_settings()
    return bool(s["host"] and (s["sender"] or s["user"]))


def send(to: list[str], subject: str, body: str) -> None:
    to = [a.strip() for a in (to or []) if a and a.strip()]
    if not to:
        raise MailError("no recipients")
    s = keystore.get_smtp_settings()
    if not s["host"]:
        raise MailError("SMTP host is not set")
    sender = s["sender"] or s["user"]
    if not sender:
        raise MailError("set a From address (or a username that is one)")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or None)
    msg.set_content(body)

    port = int(s["port"])
    ctx = ssl.create_default_context()
    try:
        if s["security"] == "ssl":
            server = smtplib.SMTP_SSL(s["host"], port, timeout=TIMEOUT_SECONDS,
                                      context=ctx)
        else:
            server = smtplib.SMTP(s["host"], port, timeout=TIMEOUT_SECONDS)
        with server:
            server.ehlo()
            if s["security"] == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
            password = keystore.get_smtp_password()
            if s["user"] and password:
                server.login(s["user"], password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"the mail server rejected the username or password ({e.smtp_code})")
    except smtplib.SMTPException as e:
        raise MailError(f"the mail server refused the message: {type(e).__name__}: {e}")
    except (OSError, ssl.SSLError) as e:
        raise MailError(f"could not reach {s['host']}:{port}: {type(e).__name__}: {e}")
    logger.info(f"mail sent to {len(to)} recipient(s): {subject!r}")
