from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager


def error_response(message: str, status_code: int = 400):
    return jsonify({"error": message, "message": message}), status_code

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
        return error_response("הלו״ז חייב להיות רשימת טווחי תאריכים.")
    if any(not isinstance(item, dict) for item in data):
        return error_response("כל טווח תאריכים חייב להיות אובייקט תקין.")
    config.set_schedule(data)
    return jsonify({"status": "ok"}), 201


@schedule_bp.route("/status", methods=["GET"])
def schedule_status():
    """What the scheduler did last, so the page can show it."""
    sched = current_app.config.get("scheduler")
    if sched is None:
        return jsonify({"active": False, "last_run": None, "running": False})
    return jsonify({"active": sched.is_alive(), "last_run": sched.last_run, "running": sched.running})
