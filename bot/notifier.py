"""Send summary emails through the Gmail API.

Credentials come ONLY from .env (see .env.example):
  MONI_SENDER           the Gmail address the mail is sent from
  GOOGLE_CLIENT_ID      OAuth client from the Google Cloud Console (Desktop app)
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN  produced once by `uv run python bot/gmail_auth.py`

The refresh token is exchanged for a short-lived access token on every send, so no
password is stored anywhere and the Gmail account needs no "app password".
"""
import base64
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Load the project-root .env. Never overrides real env vars.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from common.config_manager import ConfigManager  # noqa: E402

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailNotConfigured(RuntimeError):
    """Raised when the Gmail OAuth values are missing from .env."""


@dataclass
class Attachment:
    data: bytes
    name: str


def gmail_credentials() -> Credentials:
    """OAuth credentials from .env; raises GmailNotConfigured if any value is missing."""
    client_id = ConfigManager.get_google_client_id()
    client_secret = ConfigManager.get_google_client_secret()
    refresh_token = ConfigManager.get_google_refresh_token()
    missing = [name for name, value in (
        ("GOOGLE_CLIENT_ID", client_id),
        ("GOOGLE_CLIENT_SECRET", client_secret),
        ("GOOGLE_REFRESH_TOKEN", refresh_token),
    ) if not value]
    if missing:
        raise GmailNotConfigured(
            f"missing {', '.join(missing)} in .env — run `uv run python bot/gmail_auth.py`")
    return Credentials(
        None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[GMAIL_SEND_SCOPE],
    )


def _gmail_service():
    creds = gmail_credentials()
    creds.refresh(Request())  # trade the refresh token for an access token
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def build_message(
    sender: str,
    recipients: List[str],
    subject: str,
    content: str,
    attachments: List[Attachment],
) -> dict:
    """MIME message with PDF attachments, in the {"raw": base64url} shape Gmail expects."""
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
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")}


def send_email(
    sender: str,
    recipients: List[str],
    subject: str,
    content: str,
    attachments: List[Attachment],
) -> dict:
    """Send one email as the authorized Gmail account. Returns Gmail's message resource."""
    body = build_message(sender, recipients, subject, content, attachments)
    return _gmail_service().users().messages().send(userId="me", body=body).execute()
