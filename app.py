import os
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, g, current_app, jsonify, render_template, request


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS presence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.commit()


def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def format_message_time(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y, %I:%M %p")
    except (TypeError, ValueError):
        return datetime.now().strftime("%d/%m/%Y, %I:%M %p")


def save_message(username, message):
    clean_username = (username or "Anonymous").strip()[:30] or "Anonymous"
    clean_message = (message or "").strip()

    if not clean_message:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    cursor = db.execute(
        "INSERT INTO messages (username, message, created_at) VALUES (?, ?, ?)",
        (clean_username, clean_message, timestamp),
    )
    db.commit()

    return {
        "id": cursor.lastrowid,
        "username": clean_username,
        "message": clean_message,
        "created_at": timestamp,
        "time": format_message_time(timestamp),
    }


def get_messages(limit=250, since_id=None):
    db = get_db()
    if since_id is not None:
        rows = db.execute(
            "SELECT id, username, message, created_at FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, username, message, created_at FROM messages ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "username": row["username"],
            "message": row["message"],
            "created_at": row["created_at"],
            "time": format_message_time(row["created_at"]),
        }
        for row in rows
    ]


def prune_presence():
    cutoff = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute("DELETE FROM presence WHERE created_at < ?", (cutoff,))
    db.commit()


def get_presence_count():
    prune_presence()
    db = get_db()
    return db.execute("SELECT COUNT(*) FROM presence").fetchone()[0]


def set_presence(token):
    token_value = (token or "").strip()
    if not token_value:
        return None
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO presence (token, created_at) VALUES (?, ?)",
        (token_value, timestamp),
    )
    db.commit()
    return get_presence_count()


def remove_presence(token):
    token_value = (token or "").strip()
    if not token_value:
        return get_presence_count()
    db = get_db()
    db.execute("DELETE FROM presence WHERE token = ?", (token_value,))
    db.commit()
    return get_presence_count()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY="openchat",
        DATABASE=os.path.join(app.root_path, "chat.db"),
    )

    if test_config is not None:
        app.config.update(test_config)

    app.teardown_appcontext(close_db)

    @app.route("/")
    def home():
        return render_template("index.html")

    @app.route("/messages", methods=["GET"])
    def list_messages():
        since_id = request.args.get("since_id", type=int)
        limit = request.args.get("limit", default=80, type=int)
        return jsonify(get_messages(limit=limit, since_id=since_id))

    @app.route("/messages", methods=["POST"])
    def add_message_route():
        data = request.get_json(silent=True) or {}
        payload = save_message(data.get("username"), data.get("message"))
        if payload is None:
            return jsonify({"error": "Message cannot be empty"}), 400
        return jsonify(payload)

    @app.route("/presence", methods=["GET"])
    def presence_count():
        return jsonify({"count": get_presence_count()})

    @app.route("/presence", methods=["POST"])
    def presence_upsert():
        data = request.get_json(silent=True) or {}
        count = set_presence(data.get("token"))
        if count is None:
            return jsonify({"error": "Token is required"}), 400
        return jsonify({"count": count})

    @app.route("/presence", methods=["DELETE"])
    def presence_delete():
        data = request.get_json(silent=True) or {}
        return jsonify({"count": remove_presence(data.get("token"))})

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)