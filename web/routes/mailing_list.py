from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager

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
        return jsonify({"error": "expected a list of strings"}), 400
    config.set_mailing_list(data)
    return jsonify({"status": "ok"}), 201
