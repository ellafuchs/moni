from pathlib import Path

from flask import Flask, render_template

app = Flask(
    __name__,
    static_folder=str(Path(__file__).resolve().parent.parent / "public"),
    template_folder="templates",
)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/hello")
def hello():
    return "Hello from moni!"


if __name__ == "__main__":
    app.run(debug=True, port=3000)
