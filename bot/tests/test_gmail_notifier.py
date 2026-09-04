"""Gmail-API mailer: credentials from .env only, message built as a proper MIME mail."""
import base64
import os
import sys
from email import message_from_bytes

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))

from common.config_manager import ConfigManager  # noqa: E402
import notifier  # noqa: E402


def test_google_credentials_come_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "1//refresh")
    assert ConfigManager.get_google_client_id() == "id.apps.googleusercontent.com"
    assert ConfigManager.get_google_client_secret() == "secret"
    assert ConfigManager.get_google_refresh_token() == "1//refresh"


def test_google_credentials_none_when_missing(monkeypatch):
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert ConfigManager.get_google_client_id() is None
    assert ConfigManager.get_google_refresh_token() is None


def test_build_message_has_headers_body_and_pdf_attachment():
    raw = notifier.build_message(
        "me@example.com", ["a@example.com", "b@example.com"], "Subject!", "Hello body",
        [notifier.Attachment(b"%PDF-1.4 fake", "report.pdf")],
    )
    msg = message_from_bytes(base64.urlsafe_b64decode(raw["raw"]))
    assert msg["From"] == "me@example.com"
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Subject"] == "Subject!"
    parts = list(msg.walk())
    bodies = [p.get_payload(decode=True) for p in parts if p.get_content_type() == "text/plain"]
    assert b"Hello body" in bodies[0]
    pdfs = [p for p in parts if p.get_content_type() == "application/pdf"]
    assert len(pdfs) == 1
    assert pdfs[0].get_filename() == "report.pdf"
    assert pdfs[0].get_payload(decode=True) == b"%PDF-1.4 fake"


def test_send_email_uses_gmail_api(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "1//refresh")
    calls = {}

    class FakeSend:
        def __init__(self, body): calls["body"] = body
        def execute(self): return {"id": "msg123"}

    class FakeMessages:
        def send(self, userId, body):
            calls["userId"] = userId
            return FakeSend(body)

    class FakeUsers:
        def messages(self): return FakeMessages()

    class FakeService:
        def users(self): return FakeUsers()

    monkeypatch.setattr(notifier, "_gmail_service", lambda: FakeService())
    result = notifier.send_email("me@example.com", ["a@example.com"], "S", "B", [])
    assert result == {"id": "msg123"}
    assert calls["userId"] == "me"
    assert "raw" in calls["body"]


def test_send_email_fails_clearly_without_credentials(monkeypatch):
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(notifier.GmailNotConfigured):
        notifier.send_email("me@example.com", ["a@example.com"], "S", "B", [])
