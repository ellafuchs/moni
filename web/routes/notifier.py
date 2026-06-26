from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager

notifier_bp = Blueprint("notifier", __name__, url_prefix="/api/v1/notifier")


@notifier_bp.route("", methods=["GET"])
def get_notifier():
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify(config.get_notifier_email() or "")


@notifier_bp.route("", methods=["POST"])
def post_notifier():
    config: ConfigManager = current_app.config["config_manager"]
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    config.set_notifier_email(email)
    config.set_notifier_password(password)
    return jsonify({"status": "ok"}), 201
