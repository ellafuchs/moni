import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import List

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# move to the config
SENDER = "excelberl@gmail.com"
PASSWORD = "mzhm xnwo etwx gfej"


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
