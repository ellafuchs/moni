from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from common.config_manager import ConfigManager


def error_response(message: str, status_code: int = 400):
    return jsonify({"error": message, "message": message}), status_code

master_bp = Blueprint("master", __name__, url_prefix="/api/v1/master")


@master_bp.route("", methods=["GET"])
def get_master():
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify(config.get_last_master())


@master_bp.route("", methods=["POST"])
def post_master():
    config: ConfigManager = current_app.config["config_manager"]
    if "file" not in request.files:
        return error_response("לא נשלח קובץ להעלאה.")
    file = request.files["file"]
    if file.filename == "":
        return error_response("שם הקובץ ריק.")
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        return error_response("סוג הקובץ אינו נתמך. יש להעלות קובץ Excel בלבד.")
    files_dir = current_app.config["files_dir"]
    files_dir.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(file.filename)
    if not filename:
        return error_response("שם הקובץ אינו תקין.")
    path = files_dir / filename
    file.save(path)
    try:
        config.load_master(path)
    except (FileNotFoundError, ValueError) as error:
        return error_response(str(error))
    return jsonify({"status": "ok"}), 201
