import smtplib

from flask import Blueprint, current_app, jsonify, request

from common.config_manager import ConfigManager

notifier_bp = Blueprint("notifier", __name__, url_prefix="/api/v1/notifier")

def error_response(message: str, status_code: int = 400):
    return jsonify({"error": message, "message": message}), status_code


@notifier_bp.route("", methods=["GET"])
def get_notifier():
    config: ConfigManager = current_app.config["config_manager"]
    return jsonify({"email": config.get_notifier_email() or ""})


@notifier_bp.route("", methods=["POST"])
def post_notifier():
    config: ConfigManager = current_app.config["config_manager"]
    data = request.get_json()
    if not isinstance(data, dict):
        return error_response("גוף הבקשה חייב להיות אובייקט JSON.")
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return error_response("יש למלא אימייל וסיסמה.")
    if "@" not in email:
        return error_response("כתובת האימייל אינה תקינה.")
    if not check_email_login(email, password):
        return error_response("פרטי ההתחברות לתיבת הדואר אינם תקינים.")
    
    config.set_notifier_email(email)
    config.set_notifier_password(password)
    return jsonify({"status": "ok"}), 201


def check_email_login(email: str, password: str) -> bool:
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(email, password)
        return True
    except (OSError, smtplib.SMTPException):
        return False
