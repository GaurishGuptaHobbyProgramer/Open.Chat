import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, g, current_app, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


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

    user_table_exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone() is not None
    if user_table_exists:
        user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "email" in user_columns or "email_verified" in user_columns or "otp_code" in user_columns:
            legacy_rows = db.execute(
                "SELECT id, username, email, password_hash, created_at FROM users ORDER BY id"
            ).fetchall()
            db.execute("ALTER TABLE users RENAME TO users_legacy")
            db.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

            for row in legacy_rows:
                base_name = (row["username"] or row["email"] or "User").strip()
                if not base_name:
                    base_name = "User"
                if len(base_name) < 2:
                    base_name = f"User {row['id']}"
                db.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (f"{row['id']}, {base_name}", row["password_hash"], row["created_at"]),
                )

            db.execute("DROP TABLE users_legacy")
    else:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    for index_row in db.execute("PRAGMA index_list('users')").fetchall():
        db.execute(f"DROP INDEX IF EXISTS {index_row['name']}")

    db.execute("UPDATE users SET username = TRIM(username) WHERE username IS NOT NULL")
    db.commit()


def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def format_message_time(value):
    try:
        if value is None:
            raise ValueError("timestamp is required")

        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(text.replace(" ", "T"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%d/%m/%Y, %I:%M %p")
    except (TypeError, ValueError):
        return datetime.now().strftime("%d/%m/%Y, %I:%M %p")


def detect_device_type(user_agent):
    ua = (user_agent or "").lower()
    mobile_indicators = [
        "android",
        "iphone",
        "ipad",
        "ipod",
        "mobile",
        "blackberry",
        "windows phone",
        "opera mini",
    ]
    return "mobile" if any(marker in ua for marker in mobile_indicators) else "desktop"


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    db = get_db()
    row = db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    if row is None:
        session.clear()
        return None

    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"],
    }


def build_serial_username(display_name, db=None):
    clean_name = (display_name or "").strip()
    if len(clean_name) < 2:
        raise ValueError("Name must be at least 2 characters long.")
    if len(clean_name) > 30:
        raise ValueError("Name must be 30 characters or fewer.")

    active_db = db or get_db()
    serial_number = active_db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM users").fetchone()[0]
    return f"{serial_number}, {clean_name}"


def register_user(username, password):
    clean_name = (username or "").strip()
    if len(clean_name) < 2:
        raise ValueError("Name must be at least 2 characters long.")
    if len(clean_name) > 30:
        raise ValueError("Name must be 30 characters or fewer.")
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters long.")

    db = get_db()
    generated_username = build_serial_username(clean_name, db)
    password_hash = generate_password_hash(password)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cursor = db.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (generated_username, password_hash, timestamp),
    )
    db.commit()

    user = db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()

    session["user_id"] = user["id"]
    session.permanent = True
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user["created_at"],
    }


def authenticate_user(username, password):
    clean_username = (username or "").strip()
    db = get_db()
    user = db.execute(
        "SELECT id, username, password_hash FROM users WHERE LOWER(username) = LOWER(?)",
        (clean_username,),
    ).fetchone()

    if user is None:
        return False

    if not check_password_hash(user["password_hash"], password or ""):
        return False

    session["user_id"] = user["id"]
    session.permanent = True
    return True


def logout_user():
    session.clear()


def save_message(username, message):
    clean_username = (username or "Anonymous").strip()[:30] or "Anonymous"
    clean_message = (message or "").strip()

    if not clean_message:
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_page", mode="login"))

        device_type = detect_device_type(request.user_agent.string)
        return render_template("index.html", device_type=device_type, current_user=current_user)

    @app.route("/auth", methods=["GET", "POST"])
    def auth_page():
        device_type = detect_device_type(request.user_agent.string)
        mode = request.args.get("mode", "login")
        form_error = None
        current_user = get_current_user()

        if current_user is not None and request.method == "GET":
            return redirect(url_for("home"))

        if request.method == "POST":
            mode = request.form.get("mode", "login")
            if mode == "signup":
                username = (request.form.get("username") or "").strip()
                password = request.form.get("password") or ""
                confirm_password = request.form.get("confirm_password") or ""

                if not username or not password or not confirm_password:
                    form_error = "Please complete all signup fields."
                else:
                    try:
                        if confirm_password != password:
                            raise ValueError("Passwords do not match.")
                        register_user(username, password)
                        return redirect(url_for("home"))
                    except ValueError as exc:
                        form_error = str(exc)
                    except sqlite3.IntegrityError as exc:
                        current_app.logger.exception("Signup DB integrity failure for username=%s", username)
                        form_error = "This username is already taken."
                    except Exception as exc:
                        current_app.logger.exception("Unhandled signup failure for username=%s", username)
                        form_error = "Something went wrong while creating your account."
            else:
                username = (request.form.get("username") or "").strip()
                password = request.form.get("password") or ""
                if not username or not password:
                    form_error = "Please enter both username and password."
                elif authenticate_user(username, password):
                    return redirect(request.args.get("next") or url_for("home"))
                else:
                    form_error = "Invalid username or password."

        return render_template(
            "auth.html",
            device_type=device_type,
            current_user=current_user,
            auth_mode=mode,
            error=form_error,
        )

    @app.route("/logout", methods=["POST"])
    def logout_page():
        logout_user()
        return redirect(url_for("auth_page", mode="login"))

    @app.route("/settings")
    def settings_page():
        device_type = detect_device_type(request.user_agent.string)
        return render_template("settings.html", device_type=device_type, current_user=get_current_user())

    @app.route("/api/check-username", methods=["GET"])
    def check_username_availability():
        username = (request.args.get("username") or "").strip()
        if not username:
            return jsonify({"available": False, "message": "Please enter a name."})

        if len(username) < 2:
            return jsonify({"available": False, "message": "Name must be at least 2 characters."})

        return jsonify({
            "available": True,
            "message": "Name accepted. A serial username will be assigned automatically.",
        })

    @app.route("/messages", methods=["GET"])
    def list_messages():
        if get_current_user() is None:
            return redirect(url_for("auth_page", mode="login"))

        since_id = request.args.get("since_id", type=int)
        limit = request.args.get("limit", default=80, type=int)
        return jsonify(get_messages(limit=limit, since_id=since_id))

    @app.route("/messages", methods=["POST"])
    def add_message_route():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        payload = save_message(current_user["username"], data.get("message"))
        if payload is None:
            return jsonify({"error": "Message cannot be empty"}), 400
        return jsonify(payload)

    @app.route("/presence", methods=["GET"])
    def presence_count():
        if get_current_user() is None:
            return jsonify({"error": "Authentication required"}), 401

        return jsonify({"count": get_presence_count()})

    @app.route("/presence", methods=["POST"])
    def presence_upsert():
        if get_current_user() is None:
            return jsonify({"error": "Authentication required"}), 401

        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        count = set_presence(data.get("token"))
        if count is None:
            return jsonify({"error": "Token is required"}), 400
        return jsonify({"count": count})

    @app.route("/presence", methods=["DELETE"])
    def presence_delete():
        if get_current_user() is None:
            return jsonify({"error": "Authentication required"}), 401

        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        return jsonify({"count": remove_presence(data.get("token"))})

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)