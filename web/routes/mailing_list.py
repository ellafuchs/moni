from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager


def error_response(message: str, status_code: int = 400):
    return jsonify({"error": message, "message": message}), status_code

mailing_list_bp = Blueprint("mailing_list", __name__, url_prefix="/api/v1/mailing_list")


@mailing_list_bp.route("", methods=["GET"])
def get_mailing_list():
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify(config.get_mailing_list() or [])


@mailing_list_bp.route("", methods=["POST"])
def post_mailing_list():
    config: ConfigManager = current_app.config["config_manager"]
    data = request.get_json()
    if not isinstance(data, list):
        return error_response("רשימת התפוצה חייבת להיות רשימת כתובות אימייל.")
    if any(not isinstance(email, str) or not email.strip() for email in data):
        return error_response("כל פריט ברשימת התפוצה חייב להיות כתובת אימייל תקינה.")
    config.set_mailing_list(data)
    return jsonify({"status": "ok"}), 201
