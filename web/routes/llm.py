from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager

llm_bp = Blueprint("llm", __name__, url_prefix="/api/v1/llm")


@llm_bp.route("", methods=["GET"])
def get_llm():
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify(
        {
            "api_key": config.get_api_key() or "",
            "model_name": config.get_model_name() or "",
        }
    )


@llm_bp.route("", methods=["POST"])
def post_llm():
    config: ConfigManager = current_app.config["config_manager"]
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    api_key = data.get("api_key")
    model_name = data.get("model_name")
    if not api_key or not model_name:
        return jsonify({"error": "api_key and model_name are required"}), 400
    config.set_api_key(api_key)
    config.set_model_name(model_name)
    return jsonify({"status": "ok"}), 201
