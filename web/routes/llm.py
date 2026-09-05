from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager


def error_response(message: str, status_code: int = 400):
    return jsonify({"error": message, "message": message}), status_code

llm_bp = Blueprint("llm", __name__, url_prefix="/api/v1/llm")


@llm_bp.route("", methods=["GET"])
def get_llm():
    """Model settings. The API key lives only in .env, so we report whether the key
    for the configured provider is present rather than its value."""
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify(
        {
            "api_key_set": bool(config.get_api_key()),
            "model_name": config.get_model_name() or "",
            "model_provider": config.get_model_provider() or "openai",
            "model_fallback": config.get_model_fallback() or "",
        }
    )


@llm_bp.route("", methods=["POST"])
def post_llm():
    config: ConfigManager = current_app.config["config_manager"]
    data = request.get_json()
    if not isinstance(data, dict):
        return error_response("גוף הבקשה חייב להיות אובייקט JSON.")
    model_name = data.get("model_name")
    if not model_name:
        return error_response("יש למלא שם מודל.")
    provider = data.get("model_provider")
    if provider is not None:
        if provider not in ConfigManager.API_KEY_ENV_BY_PROVIDER:
            return error_response("ספק לא נתמך. אפשרויות: openai, google_genai.")
        config.set_model_provider(provider)
    config.set_model_name(model_name)
    if "model_fallback" in data:
        config.set_model_fallback(data.get("model_fallback") or None)
    return jsonify({"status": "ok"}), 201
