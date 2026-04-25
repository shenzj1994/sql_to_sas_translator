"""
app.py
──────
Minimal Flask web app for the SQL → SAS pass-through translator.

Routes
──────
    GET  /           renders index.html
    POST /translate  JSON in, JSON out
"""

from flask import Flask, render_template, request, jsonify
from translator import translate

app = Flask(__name__)


@app.get("/")
def index():
    """Render the main index page."""
    return render_template("index.html")


@app.post("/translate")
def do_translate():
    """Handle SQL to SAS translation requests."""
    data = request.get_json(silent=True) or {}

    sql = (data.get("sql") or "").strip()
    conn = data.get("conn") or {}
    select_mode = (data.get("select_mode") or "comment").strip().lower()

    if not sql:
        return jsonify({"error": "No SQL provided."}), 400

    if not isinstance(conn, dict):
        msg = "Connection parameters must be an object."
        return jsonify({"error": msg}), 400

    valid_modes = ("ignore", "comment", "dataset")
    if select_mode not in valid_modes:
        msg = "select_mode must be 'ignore', 'comment', or 'dataset'."
        return jsonify({"error": msg}), 400

    conn_name = (conn.get("name") or "").strip() or "myconn"
    conn_dbtype = (conn.get("dbtype") or "").strip() or "oracle"
    conn_dsn = (conn.get("dsn") or "").strip()
    conn_authdomain = (conn.get("authdomain") or "").strip()
    conn_type = (conn.get("conntype") or "").strip() or "global"

    try:
        result = translate(
            sql, conn_name, conn_dbtype, conn_dsn,
            conn_authdomain, conn_type, select_mode
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
