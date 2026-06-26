from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager

schedule_bp = Blueprint("schedule", __name__, url_prefix="/api/v1/schedule")


@schedule_bp.route("", methods=["GET"])
def get_schedule():
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify(config.get_schedule() or [])


@schedule_bp.route("", methods=["POST"])
def post_schedule():
    config: ConfigManager = current_app.config["config_manager"]
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "expected a list of schedule items"}), 400
    config.set_schedule(data)
    return jsonify({"status": "ok"}), 201
