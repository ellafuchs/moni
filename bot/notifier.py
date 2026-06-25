import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import List

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


def _load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_env()

SENDER = os.environ.get("MONI_SENDER", "")
PASSWORD = os.environ.get("MONI_PASSWORD", "")


@dataclass
class Attachment:
    data: bytes
    name: str


def send_email(
    sender: str,
    app_password: str,
    recipients: List[str],
    subject: str,
    content: str,
    attachments: List[Attachment],
):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(content)

    for attachment in attachments:
        msg.add_attachment(
            attachment.data,
            maintype="application",
            subtype="pdf",
            filename=attachment.name,
        )

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(sender, app_password)
        smtp.send_message(msg)
