from flask import Blueprint, jsonify

from common.config_manager import ConfigManager

notifier_bp = Blueprint("notifier", __name__, url_prefix="/api/v1/notifier")


@notifier_bp.route("", methods=["GET"])
def get_notifier():
    """Read-only view of the sending mailbox.

    The sender address and the Gmail OAuth values live ONLY in .env, so they cannot be
    set from the web UI. We report the address and whether everything needed to send is
    present; secrets are never returned.
    """
    email = ConfigManager.get_notifier_email()
    configured = bool(
        email
        and ConfigManager.get_google_client_id()
        and ConfigManager.get_google_client_secret()
        and ConfigManager.get_google_refresh_token()
    )
    return jsonify({"email": email or "", "configured": configured})
