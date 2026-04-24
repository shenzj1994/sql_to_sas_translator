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
    return render_template("index.html")


@app.post("/translate")
def do_translate():
    data = request.get_json(silent=True) or {}

    sql  = (data.get("sql")  or "").strip()
    conn = (data.get("conn") or "").strip()

    if not sql:
        return jsonify({"error": "No SQL provided."}), 400
    if not conn:
        return jsonify({"error": "Connection name is required."}), 400

    try:
        result = translate(sql, conn)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
