"""bot/main.py CLI: --to overrides recipients, --no-email skips sending, empty runs send nothing."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "bot"))

import pytest  # noqa: E402

import main  # noqa: E402


def test_parse_args_defaults():
    args = main.parse_args([])
    assert args.to is None
    assert args.no_email is False


def test_parse_args_to_is_repeatable():
    args = main.parse_args(["--to", "a@example.com", "--to", "b@example.com"])
    assert args.to == ["a@example.com", "b@example.com"]


def test_resolve_recipients_prefers_override():
    assert main.resolve_recipients(["me@example.com"], ["list@example.com"]) == ["me@example.com"]
    assert main.resolve_recipients(None, ["list@example.com"]) == ["list@example.com"]
    assert main.resolve_recipients(None, None) == []


def test_email_reports_sends_pdfs_to_recipients(tmp_path, monkeypatch):
    pdf = tmp_path / "x_summary.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    calls = []
    monkeypatch.setattr(main, "send_email", lambda *a: calls.append(a) or {"id": "1"})
    sent = main.email_reports("me@example.com", ["to@example.com"], [(str(pdf), "x_summary")])
    assert sent is True
    (sender, recipients, subject, body, attachments), = calls
    assert sender == "me@example.com"
    assert recipients == ["to@example.com"]
    assert [a.name for a in attachments] == ["x_summary.pdf"]
    assert attachments[0].data == b"%PDF-1.4 fake"


def test_email_reports_skips_when_nothing_rendered(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "send_email", lambda *a: calls.append(a))
    assert main.email_reports("me@example.com", ["to@example.com"], []) is False
    assert calls == []


def test_email_reports_skips_without_recipients(tmp_path, monkeypatch):
    pdf = tmp_path / "x_summary.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    calls = []
    monkeypatch.setattr(main, "send_email", lambda *a: calls.append(a))
    assert main.email_reports("me@example.com", [], [(str(pdf), "x_summary")]) is False
    assert calls == []


def test_run_stops_before_any_work_when_gmail_is_not_configured(monkeypatch):
    from notifier import GmailNotConfigured
    monkeypatch.setattr(main, "gmail_credentials",
                        lambda: (_ for _ in ()).throw(GmailNotConfigured("missing GOOGLE_REFRESH_TOKEN")))
    asked_for_letters = []
    monkeypatch.setattr(main, "get_pdf_urls", lambda: asked_for_letters.append(1) or [])
    with pytest.raises(GmailNotConfigured):
        main.main([])
    assert asked_for_letters == []


def test_no_email_run_does_not_touch_gmail(monkeypatch):
    checked = []
    monkeypatch.setattr(main, "gmail_credentials", lambda: checked.append(1))
    monkeypatch.setattr(main, "ConfigManager", lambda path: (_ for _ in ()).throw(RuntimeError("stop here")))
    with pytest.raises(RuntimeError, match="stop here"):
        main.main(["--no-email"])
    assert checked == []
