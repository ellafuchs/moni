from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager

master_bp = Blueprint("master", __name__, url_prefix="/api/v1/master")


@master_bp.route("", methods=["GET"])
def get_master():
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify(config.get_last_master())


@master_bp.route("", methods=["POST"])
def post_master():
    config: ConfigManager = current_app.config["config_manager"]
    if "file" not in request.files:
        return jsonify({"error": "no file provided"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "empty filename"}), 400
    path = f"./files/{file.filename}"
    file.save(path)
    config.load_master(path)
    return jsonify({"status": "ok"}), 201
