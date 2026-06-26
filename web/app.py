from flask import Flask

from common.config_manager import ConfigManager
from web.routes import register_routes

app = Flask(__name__)
app.config["config_manager"] = ConfigManager("./files/config.json")

register_routes(app)
