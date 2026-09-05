"""One-time Gmail authorization: turn the OAuth client in .env into a refresh token.

    uv run python bot/gmail_auth.py

Prerequisites (Google Cloud Console, https://console.cloud.google.com):
  1. Create a project and enable the "Gmail API".
  2. Configure the OAuth consent screen (External, Testing) and add the sending Gmail
     address as a test user.
  3. Create an OAuth client ID of type "Desktop app"; put its client id and secret in
     .env as GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.

This script opens the browser for Google's consent page (log in as MONI_SENDER, allow
"Send email on your behalf"), then writes GOOGLE_REFRESH_TOKEN into .env. After that,
bot/notifier.py can send mail without any password.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import find_dotenv, load_dotenv, set_key
from google_auth_oauthlib.flow import InstalledAppFlow

from common.config_manager import ConfigManager
from notifier import GMAIL_SEND_SCOPE, TOKEN_URI

# Let python-dotenv find the .env file (an absolute path, wherever it is up the tree);
# the project root is only the fallback for the first run, when no .env exists yet.
ENV_PATH = Path(find_dotenv() or Path(__file__).resolve().parent.parent / ".env")
load_dotenv(ENV_PATH)


def main() -> int:
    client_id = ConfigManager.get_google_client_id()
    client_secret = ConfigManager.get_google_client_secret()
    if not client_id or not client_secret:
        print("Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first (see .env.example).")
        return 1

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=[GMAIL_SEND_SCOPE])
    print(f"Opening the browser — sign in as {ConfigManager.get_notifier_email() or 'the sending Gmail account'} "
          "and allow sending mail.")
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        print("Google did not return a refresh token. Remove the app's access at "
              "https://myaccount.google.com/permissions and run this again.")
        return 1

    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "GOOGLE_REFRESH_TOKEN", creds.refresh_token, quote_mode="never")
    print(f"GOOGLE_REFRESH_TOKEN saved to {ENV_PATH}. Restart the server so it picks it up.")
    print("Check it with: uv run python bot/send_test_mail.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
