from .master import master_bp
from .mailing_list import mailing_list_bp
from .notifier import notifier_bp
from .schedule import schedule_bp
from .llm import llm_bp


def register_routes(app):
    app.register_blueprint(master_bp)
    app.register_blueprint(mailing_list_bp)
    app.register_blueprint(notifier_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(llm_bp)
