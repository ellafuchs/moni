"""Send one test email from the .env sender to the mailing list, via the Gmail API.

    uv run python bot/send_test_mail.py                      # send to the config.json mailing list
    uv run python bot/send_test_mail.py --to me@example.com  # send only to this address
    uv run python bot/send_test_mail.py --check              # only verify the Gmail credentials

Needs MONI_SENDER, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN in
.env (create the token with `uv run python bot/gmail_auth.py`). Run from the repo root.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request

from common.config_manager import ConfigManager
from notifier import GmailNotConfigured, gmail_credentials, send_email

CONFIG_PATH = "files/config.json"


def check_credentials() -> bool:
    try:
        creds = gmail_credentials()
        creds.refresh(Request())
        return True
    except GmailNotConfigured as e:
        print(f"Gmail is not configured: {e}")
    except RefreshError as e:
        print(f"Google rejected the refresh token: {e}")
        print("Run `uv run python bot/gmail_auth.py` again to get a new one.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a test email through the Gmail API.")
    parser.add_argument("--check", action="store_true", help="only verify the credentials")
    parser.add_argument("--to", action="append", metavar="EMAIL",
                        help="send to this address instead of the mailing list (repeatable)")
    args = parser.parse_args()

    sender = ConfigManager.get_notifier_email()
    if not sender:
        print("MONI_SENDER is missing from .env")
        return 1
    print(f"sender: {sender}")
    if not check_credentials():
        return 1
    print("Gmail credentials OK")
    if args.check:
        return 0

    recipients = args.to or ConfigManager(CONFIG_PATH).get_mailing_list() or []
    if not recipients:
        print("mailing list in files/config.json is empty and no --to given")
        return 1
    print(f"sending test email to: {', '.join(recipients)}")
    result = send_email(sender, recipients, "moni – test email",
                        "This is a test email from the moni budget-letter bot.", [])
    print(f"sent, Gmail message id: {result.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
