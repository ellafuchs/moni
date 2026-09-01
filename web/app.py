from pathlib import Path

from flask import Flask, send_from_directory

from common.config_manager import ConfigManager
from web.routes import register_routes

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
FILES_DIR = Path("./files")

app = Flask(__name__)
app.config["config_manager"] = ConfigManager("./files/config.json")
app.config["files_dir"] = FILES_DIR

register_routes(app)


@app.route("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/<path:filename>")
def public_files(filename):
    return send_from_directory(PUBLIC_DIR, filename)
