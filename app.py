import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):
        return False

from flask import Flask, g, current_app, has_app_context, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

_project_root = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_project_root, ".env")
if os.path.exists(_env_path):
    with open(_env_path, "rb") as env_file:
        env_bytes = env_file.read()
    if env_bytes.startswith(b"\xef\xbb\xbf"):
        with open(_env_path, "wb") as env_file:
            env_file.write(env_bytes.lstrip(b"\xef\xbb\xbf"))
load_dotenv(_env_path)


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
            created_at TEXT NOT NULL,
            attachment_name TEXT,
            attachment_type TEXT,
            attachment_url TEXT,
            attachment_size INTEGER DEFAULT 0
        )
        """
    )

    message_columns = {row[1] for row in db.execute("PRAGMA table_info(messages)").fetchall()}
    for column_name in ["attachment_name", "attachment_type", "attachment_url", "attachment_size"]:
        if column_name not in message_columns:
            db.execute(f"ALTER TABLE messages ADD COLUMN {column_name} {'TEXT' if column_name != 'attachment_size' else 'INTEGER DEFAULT 0'}")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS presence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            personality TEXT NOT NULL,
            greeting TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
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


def ensure_profile_tables():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            display_name TEXT,
            bio TEXT,
            status TEXT,
            avatar_url TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_statuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            media_url TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL DEFAULT 'direct',
            title TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            last_message_at TEXT,
            is_archived INTEGER NOT NULL DEFAULT 0,
            is_muted INTEGER NOT NULL DEFAULT 0,
            is_pinned INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL,
            UNIQUE(conversation_id, user_id),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'message',
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            link TEXT,
            conversation_id INTEGER,
            sender_id INTEGER,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    db.commit()


def get_user_profile(user_id):
    db = get_db()
    user = db.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if user is None:
        return None

    profile = db.execute(
        "SELECT display_name, bio, status, avatar_url, updated_at FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    display_name = (profile["display_name"] if profile and profile["display_name"] else user["username"]).strip()
    bio = (profile["bio"] if profile and profile["bio"] else "").strip()
    status = (profile["status"] if profile and profile["status"] else "Available").strip() or "Available"
    avatar_url = (profile["avatar_url"] if profile and profile["avatar_url"] else "").strip()

    return {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": display_name,
        "bio": bio,
        "status": status,
        "avatar_url": avatar_url,
        "created_at": user["created_at"],
        "updated_at": profile["updated_at"] if profile else None,
    }


def update_user_profile(user_id, display_name=None, bio=None, status=None, avatar_url=None):
    user = get_current_user() if session.get("user_id") == user_id else None
    if user is None and user_id is None:
        raise ValueError("User not found")

    db = get_db()
    existing = db.execute(
        "SELECT id FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if existing is None:
        raise ValueError("User not found")

    clean_display_name = (display_name or "").strip() or "User"
    if len(clean_display_name) > 60:
        clean_display_name = clean_display_name[:60]

    clean_bio = (bio or "").strip()
    if len(clean_bio) > 200:
        clean_bio = clean_bio[:200]

    clean_status = (status or "Available").strip() or "Available"
    if len(clean_status) > 80:
        clean_status = clean_status[:80]

    clean_avatar_url = (avatar_url or "").strip()
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    db.execute(
        """
        INSERT INTO user_profiles (user_id, display_name, bio, status, avatar_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            display_name = excluded.display_name,
            bio = excluded.bio,
            status = excluded.status,
            avatar_url = excluded.avatar_url,
            updated_at = excluded.updated_at
        """,
        (user_id, clean_display_name, clean_bio, clean_status, clean_avatar_url, updated_at),
    )
    db.commit()
    return get_user_profile(user_id)


def search_users(query, current_user_id=None):
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    db = get_db()
    search_term = f"%{clean_query}%"
    conditions = [
        "(LOWER(u.username) LIKE LOWER(?) OR LOWER(COALESCE(p.display_name, '')) LIKE LOWER(?))",
    ]
    parameters = [search_term, search_term]
    if current_user_id is not None:
        conditions.append("u.id != ?")
        parameters.append(current_user_id)

    rows = db.execute(
        f"""
        SELECT u.id, u.username, COALESCE(p.display_name, '') AS display_name,
               COALESCE(p.bio, '') AS bio, COALESCE(p.status, 'Available') AS status,
               COALESCE(p.avatar_url, '') AS avatar_url
        FROM users u
        LEFT JOIN user_profiles p ON p.user_id = u.id
        WHERE {' AND '.join(conditions)}
        ORDER BY u.id ASC
        LIMIT 20
        """,
        tuple(parameters),
    ).fetchall()

    return [
        {
            "id": row["id"],
            "username": row["username"],
            "display_name": (row["display_name"] or row["username"]).strip(),
            "bio": (row["bio"] or "").strip(),
            "status": (row["status"] or "Available").strip() or "Available",
            "avatar_url": (row["avatar_url"] or "").strip(),
        }
        for row in rows
    ]


def start_conversation_for_users(user_a_id, user_b_id):
    if user_a_id == user_b_id:
        return None

    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        for user_id in (user_a_id, user_b_id):
            if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise ValueError("User not found")

        row = db.execute(
            """
            SELECT c.id
            FROM conversations c
            JOIN conversation_members cm ON cm.conversation_id = c.id
            WHERE c.kind = 'direct'
            GROUP BY c.id
            HAVING COUNT(*) = 2
               AND SUM(CASE WHEN cm.user_id IN (?, ?) THEN 1 ELSE 0 END) = 2
            ORDER BY c.id
            LIMIT 1
            """,
            (user_a_id, user_b_id),
        ).fetchone()

        if row is not None:
            db.commit()
            return row["id"]

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = db.execute(
            "INSERT INTO conversations (kind, title, created_by, created_at, last_message_at, is_archived, is_muted, is_pinned) VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
            ("direct", "Direct Chat", user_a_id, now, now),
        )
        conversation_id = cursor.lastrowid
        db.execute(
            "INSERT INTO conversation_members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (conversation_id, user_a_id, now),
        )
        db.execute(
            "INSERT INTO conversation_members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (conversation_id, user_b_id, now),
        )
        db.commit()
        return conversation_id
    except Exception:
        db.rollback()
        raise


def create_group_conversation(created_by, title, member_user_ids=None):
    clean_title = (title or "").strip() or "Group Chat"
    if len(clean_title) > 80:
        clean_title = clean_title[:80].strip()

    if created_by is None:
        raise ValueError("A creator is required for group chats.")

    db = get_db()
    creator = db.execute("SELECT id FROM users WHERE id = ?", (created_by,)).fetchone()
    if creator is None:
        raise ValueError("Creator not found")

    member_ids = []
    for value in member_user_ids or []:
        try:
            member_id = int(value)
        except (TypeError, ValueError):
            continue
        if member_id not in member_ids:
            member_ids.append(member_id)

    if created_by not in member_ids:
        member_ids.insert(0, created_by)

    if not member_ids:
        raise ValueError("At least one group member is required.")

    for member_id in member_ids:
        if db.execute("SELECT 1 FROM users WHERE id = ?", (member_id,)).fetchone() is None:
            raise ValueError(f"User {member_id} not found")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor = db.execute(
        "INSERT INTO conversations (kind, title, created_by, created_at, last_message_at, is_archived, is_muted, is_pinned) VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
        ("group", clean_title, created_by, now, now),
    )
    conversation_id = cursor.lastrowid

    for member_id in member_ids:
        db.execute(
            "INSERT OR IGNORE INTO conversation_members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (conversation_id, member_id, now),
        )

    db.commit()
    return conversation_id


def add_group_member(conversation_id, user_id):
    if conversation_id is None or user_id is None:
        return False

    db = get_db()
    conversation = db.execute(
        "SELECT id, kind FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if conversation is None or conversation["kind"] != "group":
        return False

    user = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor = db.execute(
        "INSERT OR IGNORE INTO conversation_members (conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
        (conversation_id, user_id, now),
    )
    db.commit()
    return cursor.rowcount > 0


def create_call_session(caller_id, target_id=None, mode="audio", conversation_id=None):
    raise ValueError("Voice and video calls are under development")


def update_call_status(call_id, status):
    raise ValueError("Voice and video calls are under development")



def save_call_signal(session_id, sender_id, target_id, signal_type, payload):
    raise ValueError("Voice and video calls are under development")


def get_call_signals(session_id, user_id, after_id=0):
    return []


def ensure_call_tables():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS call_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id INTEGER NOT NULL,
            target_id INTEGER,
            conversation_id INTEGER,
            mode TEXT NOT NULL DEFAULT 'audio',
            status TEXT NOT NULL DEFAULT 'ringing',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(caller_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(target_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
        )
        """
    )
    db.commit()


def ensure_notification_tables():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'message',
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            link TEXT,
            conversation_id INTEGER,
            sender_id INTEGER,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_read_state (
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_read_at TEXT,
            PRIMARY KEY (conversation_id, user_id),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_typing (
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            is_typing INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (conversation_id, user_id),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.commit()


def ensure_application_event_table():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            detail TEXT NOT NULL,
            endpoint TEXT,
            response_status INTEGER,
            user_id INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_application_events_created_at ON application_events(created_at)")
    db.commit()


def record_application_event(event_type, detail, endpoint=None, response_status=None, user_id=None):
    try:
        db = get_db()
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute(
            "INSERT INTO application_events (event_type, detail, endpoint, response_status, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ((event_type or "event")[:40], (detail or "")[:240], (endpoint or "")[:120] or None, response_status, user_id, created_at),
        )
        db.execute(
            "DELETE FROM application_events WHERE id NOT IN (SELECT id FROM application_events ORDER BY id DESC LIMIT 1000)"
        )
        db.commit()
    except sqlite3.Error:
        if "db" in g:
            g.db.rollback()
        current_app.logger.exception("Could not record application event")


def get_configured_developer_user_id():
    try:
        developer_id = int(current_app.config.get("OPENCHAT_DEVELOPER_USER_ID"))
    except (TypeError, ValueError):
        return None
    return developer_id if developer_id > 0 else None


def is_developer_user(user=None):
    active_user = user if user is not None else get_current_user()
    developer_id = get_configured_developer_user_id()
    return active_user is not None and developer_id is not None and active_user["id"] == developer_id


def mark_conversation_read(conversation_id, user_id):
    if conversation_id is None or user_id is None:
        return False

    db = get_db()
    member = db.execute(
        "SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user_id),
    ).fetchone()
    if member is None:
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "INSERT INTO conversation_read_state (conversation_id, user_id, last_read_at) VALUES (?, ?, ?) ON CONFLICT(conversation_id, user_id) DO UPDATE SET last_read_at = excluded.last_read_at",
        (conversation_id, user_id, now),
    )
    db.commit()
    return True


def set_typing_state(conversation_id, user_id, is_typing):
    if conversation_id is None or user_id is None:
        return {"conversation_id": conversation_id, "user_id": user_id, "is_typing": False, "updated_at": None}

    db = get_db()
    member = db.execute(
        "SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user_id),
    ).fetchone()
    if member is None:
        return {"conversation_id": conversation_id, "user_id": user_id, "is_typing": False, "updated_at": None}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = bool(is_typing)
    db.execute(
        "INSERT INTO conversation_typing (conversation_id, user_id, is_typing, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id, user_id) DO UPDATE SET is_typing = excluded.is_typing, updated_at = excluded.updated_at",
        (conversation_id, user_id, 1 if state else 0, now),
    )
    db.commit()
    return {"conversation_id": conversation_id, "user_id": user_id, "is_typing": state, "updated_at": now}


def get_typing_state(conversation_id, user_id):
    if conversation_id is None or user_id is None:
        return {"conversation_id": conversation_id, "user_id": user_id, "is_typing": False, "updated_at": None}

    db = get_db()
    row = db.execute(
        "SELECT conversation_id, user_id, is_typing, updated_at FROM conversation_typing WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user_id),
    ).fetchone()

    if row is None:
        return {"conversation_id": conversation_id, "user_id": user_id, "is_typing": False, "updated_at": None}

    return {
        "conversation_id": row["conversation_id"],
        "user_id": row["user_id"],
        "is_typing": bool(row["is_typing"]),
        "updated_at": row["updated_at"],
    }


def create_direct_chat(user_a_id, user_b_id):
    return start_conversation_for_users(user_a_id, user_b_id)


def ensure_conversation_message_tables():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            reply_to INTEGER,
            attachment_name TEXT,
            attachment_type TEXT,
            attachment_url TEXT,
            attachment_size INTEGER DEFAULT 0,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    message_columns = {row[1] for row in db.execute("PRAGMA table_info(conversation_messages)").fetchall()}
    for column_name, column_type in {
        "attachment_name": "TEXT",
        "attachment_type": "TEXT",
        "attachment_url": "TEXT",
        "attachment_size": "INTEGER DEFAULT 0",
        "legacy_message_id": "INTEGER",
    }.items():
        if column_name not in message_columns:
            db.execute(f"ALTER TABLE conversation_messages ADD COLUMN {column_name} {column_type}")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_read_state (
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_read_at TEXT,
            PRIMARY KEY (conversation_id, user_id),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.commit()


def save_user_status(user_id, text, media_url=None, expires_at=None):
    if user_id is None:
        raise ValueError("User required")

    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Status text is required")

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise ValueError("User not found")

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor = db.execute(
        "INSERT INTO user_statuses (user_id, text, media_url, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, clean_text, (media_url or "").strip() or None, created_at, expires_at),
    )
    db.commit()
    return {
        "id": cursor.lastrowid,
        "user_id": user_id,
        "text": clean_text,
        "media_url": (media_url or "").strip() or None,
        "created_at": created_at,
        "expires_at": expires_at,
    }


def get_user_statuses(user_id, limit=10):
    if user_id is None:
        return []

    db = get_db()
    rows = db.execute(
        "SELECT id, user_id, text, media_url, created_at, expires_at FROM user_statuses WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()

    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "text": row["text"],
            "media_url": row["media_url"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }
        for row in rows
    ]


def create_notification(user_id, title, body, kind="message", link=None, conversation_id=None, sender_id=None):
    if user_id is None:
        return None

    clean_title = (title or "OpenChat").strip() or "OpenChat"
    clean_body = (body or "").strip()
    if not clean_body:
        return None

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise ValueError("User not found")

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor = db.execute(
        "INSERT INTO user_notifications (user_id, kind, title, body, link, conversation_id, sender_id, is_read, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (user_id, (kind or "message").strip() or "message", clean_title, clean_body, (link or "").strip() or None, conversation_id, sender_id, created_at),
    )
    db.commit()
    return {
        "id": cursor.lastrowid,
        "user_id": user_id,
        "kind": (kind or "message").strip() or "message",
        "title": clean_title,
        "body": clean_body,
        "link": (link or "").strip() or None,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "is_read": False,
        "created_at": created_at,
    }


def get_user_notifications(user_id, unread_only=False, limit=25):
    if user_id is None:
        return []

    db = get_db()
    query = "SELECT id, user_id, kind, title, body, link, conversation_id, sender_id, is_read, created_at FROM user_notifications WHERE user_id = ?"
    params = [user_id]
    if unread_only:
        query += " AND is_read = 0"
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, tuple(params)).fetchall()
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "kind": row["kind"],
            "title": row["title"],
            "body": row["body"],
            "link": row["link"],
            "conversation_id": row["conversation_id"],
            "sender_id": row["sender_id"],
            "is_read": bool(row["is_read"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def mark_notification_read(user_id, notification_id):
    if user_id is None or notification_id is None:
        return False

    db = get_db()
    cursor = db.execute(
        "UPDATE user_notifications SET is_read = 1 WHERE user_id = ? AND id = ? AND is_read = 0",
        (user_id, notification_id),
    )
    db.commit()
    return cursor.rowcount > 0


def send_conversation_message(conversation_id, sender_id, text, status="sent", reply_to=None, attachment_name=None, attachment_type=None, attachment_url=None, attachment_size=0):
    if conversation_id is None:
        raise ValueError("Conversation required")

    clean_text = (text or "").strip()
    clean_attachment_name = (attachment_name or "").strip() or None
    clean_attachment_type = (attachment_type or "").strip() or None
    clean_attachment_url = (attachment_url or "").strip() or None
    clean_attachment_size = int(attachment_size or 0)

    if not clean_text and not clean_attachment_url and not clean_attachment_name:
        return None

    db = get_db()
    if conversation_id == 1:
        ensure_enormous_member(sender_id, db)

    member = db.execute(
        "SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, sender_id),
    ).fetchone()
    if member is None:
        raise ValueError("Sender is not a member of this conversation")

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor = db.execute(
        """
        INSERT INTO conversation_messages (
            conversation_id, sender_id, text, created_at, status, reply_to,
            attachment_name, attachment_type, attachment_url, attachment_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            sender_id,
            clean_text,
            created_at,
            status,
            reply_to,
            clean_attachment_name,
            clean_attachment_type,
            clean_attachment_url,
            clean_attachment_size,
        ),
    )
    db.execute(
        "UPDATE conversations SET last_message_at = ? WHERE id = ?",
        (created_at, conversation_id),
    )
    db.commit()

    user = db.execute(
        "SELECT u.username, COALESCE(p.display_name, u.username) AS display_name FROM users u LEFT JOIN user_profiles p ON p.user_id = u.id WHERE u.id = ?",
        (sender_id,),
    ).fetchone()
    sender_username = user["username"] if user else f"User {sender_id}"
    sender_name = user["display_name"] if user else sender_username

    message_payload = {
        "id": cursor.lastrowid,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "username": sender_username,
        "text": clean_text,
        "message": clean_text,
        "created_at": created_at,
        "time": format_message_time(created_at),
        "status": status,
        "reply_to": reply_to,
        "attachment_name": clean_attachment_name,
        "attachment_type": clean_attachment_type,
        "attachment_url": clean_attachment_url,
        "attachment_size": clean_attachment_size,
    }

    if conversation_id != 1:
        recipients = db.execute(
            "SELECT user_id FROM conversation_members WHERE conversation_id = ? AND user_id != ?",
            (conversation_id, sender_id),
        ).fetchall()
        for recipient in recipients:
            notification_body = clean_text or (clean_attachment_name or "New media")
            create_notification(
                recipient["user_id"],
                "New message",
                notification_body,
                kind="message",
                conversation_id=conversation_id,
                sender_id=sender_id,
                link=f"/conversations/{conversation_id}",
            )

    return message_payload


def get_conversation_messages(conversation_id, user_id=None, limit=80, since_id=None):
    db = get_db()
    if since_id is not None:
        rows = db.execute(
            """
            SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at, m.status, m.reply_to,
                   m.attachment_name, m.attachment_type, m.attachment_url, m.attachment_size,
                   u.username AS sender_username,
                   COALESCE(p.display_name, u.username) AS sender_name
            FROM conversation_messages m
            LEFT JOIN users u ON u.id = m.sender_id
            LEFT JOIN user_profiles p ON p.user_id = u.id
            WHERE m.conversation_id = ? AND m.id > ?
            ORDER BY m.id ASC
            LIMIT ?
            """,
            (conversation_id, since_id, limit),
        ).fetchall()
        message_rows = rows
    else:
        rows = db.execute(
            """
            SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at, m.status, m.reply_to,
                   m.attachment_name, m.attachment_type, m.attachment_url, m.attachment_size,
                   u.username AS sender_username,
                   COALESCE(p.display_name, u.username) AS sender_name
            FROM conversation_messages m
            LEFT JOIN users u ON u.id = m.sender_id
            LEFT JOIN user_profiles p ON p.user_id = u.id
            WHERE m.conversation_id = ?
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
        message_rows = list(reversed(rows))

    messages = []
    latest_timestamp = None
    for row in message_rows:
        sender_username = row["sender_username"] or f"User {row['sender_id']}"
        sender_name = row["sender_name"] or sender_username
        latest_timestamp = row["created_at"]
        messages.append({
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "sender_id": row["sender_id"],
            "sender_name": sender_name,
            "username": sender_username,
            "text": row["text"],
            "message": row["text"],
            "created_at": row["created_at"],
            "time": format_message_time(row["created_at"]),
            "status": row["status"],
            "reply_to": row["reply_to"],
            "attachment_name": row["attachment_name"],
            "attachment_type": row["attachment_type"],
            "attachment_url": row["attachment_url"],
            "attachment_size": row["attachment_size"],
        })

    if user_id is not None and latest_timestamp is not None:
        db.execute(
            """
            INSERT INTO conversation_read_state (conversation_id, user_id, last_read_at)
            VALUES (?, ?, ?)
            ON CONFLICT(conversation_id, user_id) DO UPDATE SET
                last_read_at = MAX(excluded.last_read_at, conversation_read_state.last_read_at)
            """,
            (conversation_id, user_id, latest_timestamp),
        )
        db.commit()

    return messages


def get_user_conversations(user_id):
    if user_id is None:
        return []
    db = get_db()
    ensure_enormous_member(user_id, db)

    rows = db.execute(
        """
        SELECT c.id AS conversation_id, c.kind, c.title, c.last_message_at, c.is_pinned, c.is_archived, c.is_muted,
               m.id AS last_message_id, m.text AS last_message_text, m.attachment_name AS last_attachment_name, m.sender_id,
               (SELECT COUNT(*) FROM conversation_messages cm
                WHERE cm.conversation_id = c.id
                AND cm.sender_id != ?
                AND cm.created_at > COALESCE((SELECT last_read_at FROM conversation_read_state WHERE conversation_id = c.id AND user_id = ?), '')) AS unread_count
        FROM conversations c
        LEFT JOIN conversation_messages m ON m.id = (
            SELECT cm2.id FROM conversation_messages cm2 WHERE cm2.conversation_id = c.id ORDER BY cm2.id DESC LIMIT 1
        )
        JOIN conversation_members cmembers ON cmembers.conversation_id = c.id AND cmembers.user_id = ?
        ORDER BY c.is_pinned DESC, c.last_message_at DESC, c.id DESC
        """,
        (user_id, user_id, user_id),
    ).fetchall()

    result = []
    for row in rows:
        is_enormous = (row["conversation_id"] == 1)
        other_user = None
        avatar_url = ""
        if is_enormous:
            title = "ENORMOUS"
        elif row["kind"] == "group":
            title = (row["title"] or "Group chat").strip() or "Group chat"
        else:
            other_user = db.execute(
                """
                SELECT u.id, u.username, COALESCE(p.display_name, '') AS display_name, COALESCE(p.avatar_url, '') AS avatar_url
                FROM conversation_members cm
                JOIN users u ON u.id = cm.user_id
                LEFT JOIN user_profiles p ON p.user_id = u.id
                WHERE cm.conversation_id = ? AND cm.user_id != ?
                LIMIT 1
                """,
                (row["conversation_id"], user_id),
            ).fetchone()
            if other_user:
                display_name = (other_user["display_name"] or other_user["username"]).strip()
                title = display_name
                avatar_url = other_user["avatar_url"] or ""
            else:
                title = "Direct chat"

        last_text = row["last_message_text"]
        if not last_text and row["last_attachment_name"]:
            last_text = f"Attachment: {row['last_attachment_name']}"

        result.append({
            "conversation_id": row["conversation_id"],
            "kind": row["kind"],
            "title": title,
            "avatar_url": avatar_url,
            "last_message_at": row["last_message_at"],
            "last_message_text": last_text or "",
            "is_pinned": bool(row["is_pinned"]),
            "is_archived": bool(row["is_archived"]),
            "is_muted": bool(row["is_muted"]),
            "unread_count": row["unread_count"] or 0,
            "other_user_id": other_user["id"] if other_user else None,
            "is_enormous": is_enormous,
        })
    return result



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

    try:
        ensure_enormous_member(user["id"], db)
    except Exception:
        pass

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


def get_idle_ai_defaults():
    return {
        "name": "Idle AI from OpenChat",
        "personality": "warm, curious, and easygoing",
        "greeting": "Tell me what is on your mind, and we can keep the conversation flowing.",
    }


def get_user_idle_settings(user_id):
    defaults = get_idle_ai_defaults()
    if user_id is None:
        return defaults

    db = get_db()
    row = db.execute(
        "SELECT name, personality, greeting FROM user_settings WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if row is None:
        return defaults

    return {
        "name": (row["name"] or defaults["name"]).strip() or defaults["name"],
        "personality": (row["personality"] or defaults["personality"]).strip() or defaults["personality"],
        "greeting": (row["greeting"] or defaults["greeting"]).strip() or defaults["greeting"],
    }


def save_user_idle_settings(user_id, settings):
    if user_id is None:
        return get_idle_ai_defaults()

    defaults = get_idle_ai_defaults()
    sanitized = {
        "name": (settings or {}).get("name") or defaults["name"],
        "personality": (settings or {}).get("personality") or defaults["personality"],
        "greeting": (settings or {}).get("greeting") or defaults["greeting"],
    }

    sanitized["name"] = (sanitized["name"] or defaults["name"]).strip()[:60] or defaults["name"]
    sanitized["personality"] = (sanitized["personality"] or defaults["personality"]).strip()[:120] or defaults["personality"]
    sanitized["greeting"] = (sanitized["greeting"] or defaults["greeting"]).strip()[:220] or defaults["greeting"]

    db = get_db()
    db.execute(
        """
        INSERT INTO user_settings (user_id, name, personality, greeting)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            personality = excluded.personality,
            greeting = excluded.greeting
        """,
        (
            user_id,
            sanitized["name"],
            sanitized["personality"],
            sanitized["greeting"],
        ),
    )
    db.commit()
    return sanitized


def get_ai_backend_settings():
    openrouter_base_url = (os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").strip() or "https://openrouter.ai/api/v1"
    openrouter_model = (os.getenv("OPENROUTER_MODEL") or "openrouter/free").strip() or "openrouter/free"
    openrouter_api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    api_key = openrouter_api_key
    return {
        "enabled": str(os.getenv("OPENCHAT_AI_ENABLED", "false")).strip().lower() not in {"0", "false", "no", "off", ""},
        "api_key": api_key,
        "model": openrouter_model,
        "base_url": openrouter_base_url,
        "openai_api_key": api_key,
        "openai_model": openrouter_model,
        "openai_base_url": openrouter_base_url,
        "ollama_base_url": (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").strip() or "http://localhost:11434",
        "ollama_model": (os.getenv("OLLAMA_MODEL") or "llama3.1").strip() or "llama3.1",
    }


def is_ai_enabled():
    settings = get_ai_backend_settings()
    return settings["enabled"] and bool(settings["api_key"])


def call_llm_backend(message, idle_settings, username=None, history=None):
    if not is_ai_enabled():
        return None

    prompt = (message or "").strip()
    if not prompt:
        return None

    bot_name = idle_settings.get("name") or get_idle_ai_defaults()["name"]
    personality = idle_settings.get("personality") or get_idle_ai_defaults()["personality"]
    display_name = (username or "friend").strip() or "friend"
    system_prompt = (
        f"You are {bot_name}, a friendly and thoughtful AI companion inside OpenChat. "
        f"Your personality is {personality}. "
        f"Speak naturally, warmly, and directly to the user named {display_name}. "
        "Keep replies conversational, helpful, and tailored to what the user actually says. "
        "Avoid repeating the same canned phrasing."
    )

    prior_history = history or []
    messages = [{"role": "system", "content": system_prompt}]
    for entry in prior_history[-12:]:
        if not isinstance(entry, dict):
            continue
        role = (entry.get("role") or "").strip().lower()
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        if role == "user":
            messages.append({"role": "user", "content": text})
        elif role == "ai":
            messages.append({"role": "assistant", "content": text})

    messages.append({"role": "user", "content": prompt})

    settings = get_ai_backend_settings()
    api_key = settings["api_key"]

    if api_key:
        payload = {
            "model": settings["model"],
            "messages": messages,
            "temperature": 0.9,
            "max_tokens": 500,
        }
        data = json.dumps(payload).encode("utf-8")
        request_url = f"{settings['base_url'].rstrip('/')}/chat/completions"
        req = urllib_request.Request(
            request_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                choices = result.get("choices") or []
                if not choices:
                    return None

                first_choice = choices[0]
                if not isinstance(first_choice, dict):
                    return None

                message_payload = first_choice.get("message") or {}
                content = message_payload.get("content")
                if isinstance(content, list):
                    text_bits = []
                    for item in content:
                        if isinstance(item, dict):
                            text_bits.append(item.get("text") or item.get("content") or "")
                        elif isinstance(item, str):
                            text_bits.append(item)
                    content = "".join(text_bits)

                return (content or "").strip() or None
        except (urllib_error.HTTPError, urllib_error.URLError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            if has_app_context():
                status_code = getattr(exc, "code", None)
                if status_code == 429:
                    current_app.logger.warning("OpenRouter rate limit reached while generating idle AI response.")
                else:
                    current_app.logger.warning("OpenRouter request failed while generating idle AI response.")
            pass

    ollama_base = settings["ollama_base_url"].rstrip("/")
    ollama_model = settings["ollama_model"]
    ollama_url = f"{ollama_base}/api/chat"
    payload = {
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    try:
        req = urllib_request.Request(
            ollama_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            content = result.get("message", {}).get("content")
            return (content or "").strip() or None
    except Exception:
        return None


def generate_idle_ai_response(message, idle_settings, username=None, history=None):
    if not is_ai_enabled():
        return "Under Development — Coming Soon"

    prompt = (message or "").strip()
    if not prompt:
        return idle_settings.get("greeting", get_idle_ai_defaults()["greeting"])

    settings = get_ai_backend_settings()
    if not settings["api_key"]:
        return "Under Development — Coming Soon"

    llm_reply = call_llm_backend(prompt, idle_settings, username=username, history=history)
    if llm_reply:
        return llm_reply

    return "Under Development — Coming Soon"


def ensure_enormous_member(user_id, db=None):
    if not user_id:
        return
    active_db = db or get_db()
    ensure_enormous_space(active_db)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    active_db.execute(
        "INSERT OR IGNORE INTO conversation_members (conversation_id, user_id, role, joined_at) VALUES (1, ?, 'member', ?)",
        (user_id, now),
    )
    active_db.commit()


def ensure_enormous_space(db=None):
    active_db = db or get_db()
    active_db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL DEFAULT 'direct',
            title TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            last_message_at TEXT,
            is_archived INTEGER NOT NULL DEFAULT 0,
            is_muted INTEGER NOT NULL DEFAULT 0,
            is_pinned INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    active_db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL,
            UNIQUE(conversation_id, user_id),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    active_db.execute(
        """
        CREATE TABLE IF NOT EXISTS enormous_space (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            is_official INTEGER NOT NULL DEFAULT 1,
            is_pinned INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    row = active_db.execute(
        "SELECT id, name, slug, is_official, is_pinned, created_at FROM enormous_space WHERE id = 1"
    ).fetchone()
    if row is None:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        active_db.execute(
            "INSERT INTO enormous_space (id, name, slug, is_official, is_pinned, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "ENORMOUS", "enormous", 1, 1, created_at),
        )
        active_db.commit()
        row = active_db.execute(
            "SELECT id, name, slug, is_official, is_pinned, created_at FROM enormous_space WHERE id = 1"
        ).fetchone()

    # The canonical ENORMOUS conversation is always conversation ID 1
    conversation = active_db.execute(
        "SELECT id FROM conversations WHERE id = 1"
    ).fetchone()
    if conversation is None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        active_db.execute(
            "INSERT INTO conversations (id, kind, title, created_by, created_at, last_message_at, is_archived, is_muted, is_pinned) VALUES (1, 'group', 'ENORMOUS', NULL, ?, ?, 0, 0, 1)",
            (now, now),
        )
        active_db.commit()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    member_rows = active_db.execute("SELECT id FROM users ORDER BY id ASC").fetchall()
    for member_row in member_rows:
        active_db.execute(
            "INSERT OR IGNORE INTO conversation_members (conversation_id, user_id, role, joined_at) VALUES (1, ?, 'member', ?)",
            (member_row["id"], now),
        )
    active_db.commit()

    return dict(row) if row is not None else {
        "id": 1,
        "name": "ENORMOUS",
        "slug": "enormous",
        "is_official": 1,
        "is_pinned": 1,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def migrate_legacy_messages(db=None):
    active_db = db or get_db()
    ensure_enormous_space(active_db)

    has_messages = active_db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'messages'").fetchone()
    if not has_messages:
        return

    cm_columns = {col[1] for col in active_db.execute("PRAGMA table_info(conversation_messages)").fetchall()}
    if "legacy_message_id" not in cm_columns:
        active_db.execute("ALTER TABLE conversation_messages ADD COLUMN legacy_message_id INTEGER")
        active_db.commit()

    unmigrated = active_db.execute(
        """
        SELECT m.id, m.username, m.message, m.created_at, m.attachment_name, m.attachment_type, m.attachment_url, m.attachment_size
        FROM messages m
        WHERE m.id NOT IN (
            SELECT legacy_message_id FROM conversation_messages WHERE legacy_message_id IS NOT NULL
        )
        ORDER BY m.id ASC
        """
    ).fetchall()

    if not unmigrated:
        return

    users = active_db.execute("SELECT id, username FROM users ORDER BY id ASC").fetchall()
    default_user_id = users[0]["id"] if users else 1
    user_map = {}
    for u in users:
        u_name = u["username"].strip()
        user_map[u_name.lower()] = u["id"]
        if "," in u_name:
            user_map[u_name.split(",", 1)[1].strip().lower()] = u["id"]

    for row in unmigrated:
        msg_user = (row["username"] or "").strip()
        sender_id = user_map.get(msg_user.lower())
        if sender_id is None and "," in msg_user:
            sender_id = user_map.get(msg_user.split(",", 1)[1].strip().lower())
        if sender_id is None:
            sender_id = default_user_id

        active_db.execute(
            "INSERT OR IGNORE INTO conversation_members (conversation_id, user_id, role, joined_at) VALUES (1, ?, 'member', ?)",
            (sender_id, row["created_at"]),
        )

        created_at = row["created_at"]
        if created_at and not created_at.endswith("Z") and "T" not in created_at:
            created_at = created_at.replace(" ", "T") + "Z"

        text = row["message"] or ""
        active_db.execute(
            """
            INSERT INTO conversation_messages (
                conversation_id, sender_id, text, created_at, status, reply_to,
                attachment_name, attachment_type, attachment_url, attachment_size, legacy_message_id
            ) VALUES (1, ?, ?, ?, 'sent', NULL, ?, ?, ?, ?, ?)
            """,
            (
                sender_id,
                text,
                created_at,
                row["attachment_name"],
                row["attachment_type"],
                row["attachment_url"],
                row["attachment_size"] or 0,
                row["id"],
            ),
        )

    latest_msg = active_db.execute(
        "SELECT created_at FROM conversation_messages WHERE conversation_id = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if latest_msg:
        active_db.execute(
            "UPDATE conversations SET last_message_at = ? WHERE id = 1",
            (latest_msg["created_at"],),
        )

    active_db.commit()



def save_message(username, message, attachment_name=None, attachment_type=None, attachment_url=None, attachment_size=0):
    clean_username = (username or "Anonymous").strip()[:30] or "Anonymous"
    clean_message = (message or "").strip()

    if not clean_message and not attachment_name:
        return None

    clean_attachment_name = (attachment_name or "").strip() or None
    clean_attachment_type = (attachment_type or "").strip() or None
    clean_attachment_url = (attachment_url or "").strip() or None
    clean_attachment_size = int(attachment_size or 0)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = get_db()
    cursor = db.execute(
        "INSERT INTO messages (username, message, created_at, attachment_name, attachment_type, attachment_url, attachment_size) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            clean_username,
            clean_message,
            timestamp,
            clean_attachment_name,
            clean_attachment_type,
            clean_attachment_url,
            clean_attachment_size,
        ),
    )
    db.commit()

    return {
        "id": cursor.lastrowid,
        "username": clean_username,
        "message": clean_message,
        "created_at": timestamp,
        "time": format_message_time(timestamp),
        "attachment_name": clean_attachment_name,
        "attachment_type": clean_attachment_type,
        "attachment_url": clean_attachment_url,
        "attachment_size": clean_attachment_size,
    }


def get_messages(limit=250, since_id=None):
    db = get_db()
    if since_id is not None:
        rows = db.execute(
            "SELECT id, username, message, created_at, attachment_name, attachment_type, attachment_url, attachment_size FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, username, message, created_at, attachment_name, attachment_type, attachment_url, attachment_size FROM messages ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "username": row["username"],
            "message": row["message"],
            "created_at": row["created_at"],
            "time": format_message_time(row["created_at"]),
            "attachment_name": row["attachment_name"],
            "attachment_type": row["attachment_type"],
            "attachment_url": row["attachment_url"],
            "attachment_size": row["attachment_size"],
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
    app_environment = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").strip().lower()
    configured_secret = os.getenv("SECRET_KEY")
    test_config_secret = test_config.get("SECRET_KEY") if isinstance(test_config, dict) else None

    app.config.from_mapping(
        SECRET_KEY=configured_secret or test_config_secret or "openchat-local-development-only",
        APP_ENV=app_environment,
        DATABASE=os.getenv("DATABASE_URL", os.path.join(app.root_path, "chat.db")),
        UPLOAD_FOLDER=os.path.join(app.root_path, "static", "uploads"),
        OPENCHAT_DEVELOPER_USER_ID=os.getenv("OPENCHAT_DEVELOPER_USER_ID", ""),
        OPENCHAT_AI_ENABLED=str(os.getenv("OPENCHAT_AI_ENABLED", "false")).strip().lower() not in {"0", "false", "no", "off", ""},
    )

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    if test_config is not None:
        app.config.update(test_config)

    if app.config.get("APP_ENV") in {"production", "prod"} and not (configured_secret or test_config_secret):
        raise RuntimeError("SECRET_KEY must be configured in production.")

    app.teardown_appcontext(close_db)

    def authorize_developer_api():
        current_user = get_current_user()
        if current_user is None:
            return None, (jsonify({"error": "Authentication required"}), 401)
        if not is_developer_user(current_user):
            return None, (jsonify({"error": "Developer access required"}), 403)
        return current_user, None

    @app.after_request
    def record_failed_request(response):
        if response.status_code >= 400 and request.endpoint != "static":
            try:
                user = get_current_user()
                record_application_event(
                    "failed_request",
                    f"{request.method} request returned HTTP {response.status_code}",
                    endpoint=request.endpoint,
                    response_status=response.status_code,
                    user_id=user["id"] if user else None,
                )
            except Exception:
                current_app.logger.exception("Could not record failed request")
        return response

    @app.route("/")
    def home():
        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_page", mode="login"))

        device_type = detect_device_type(request.user_agent.string)
        enormous_space = ensure_enormous_space()
        return render_template(
            "index.html",
            device_type=device_type,
            current_user=current_user,
            enormous_space=enormous_space,
            ai_enabled=is_ai_enabled(),
            is_developer=is_developer_user(current_user),
        )

    @app.route("/developer", methods=["GET"])
    def developer_control_center():
        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_page", mode="login"))
        if not is_developer_user(current_user):
            return "Developer access required", 403
        return render_template("developer.html", current_user=current_user)

    @app.route("/api/developer/users", methods=["GET"])
    def api_developer_users():
        _, denied = authorize_developer_api()
        if denied:
            return denied
        db = get_db()
        rows = db.execute(
            """
            SELECT u.id, u.username, u.created_at,
                   COALESCE(p.display_name, u.username) AS display_name,
                   COALESCE(p.status, 'Available') AS profile_status
            FROM users u
            LEFT JOIN user_profiles p ON p.user_id = u.id
            ORDER BY u.id DESC
            LIMIT 500
            """
        ).fetchall()
        return jsonify([
            {
                "id": row["id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "created_at": row["created_at"],
                "profile_status": row["profile_status"],
                "account_status": "Not Tracked",
                "last_activity": "Not Tracked",
                "online_status": "Not Tracked",
                "is_developer": row["id"] == get_configured_developer_user_id(),
            }
            for row in rows
        ])

    @app.route("/api/developer/conversations", methods=["GET"])
    def api_developer_conversations():
        _, denied = authorize_developer_api()
        if denied:
            return denied
        db = get_db()
        rows = db.execute(
            """
            SELECT c.id, c.kind, c.title, c.created_at, c.last_message_at,
                   COUNT(DISTINCT cm.user_id) AS member_count,
                   COUNT(DISTINCT msg.id) AS message_count,
                   (SELECT text FROM conversation_messages last_msg
                    WHERE last_msg.conversation_id = c.id ORDER BY last_msg.id DESC LIMIT 1) AS last_message
            FROM conversations c
            LEFT JOIN conversation_members cm ON cm.conversation_id = c.id
            LEFT JOIN conversation_messages msg ON msg.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.id DESC
            LIMIT 500
            """
        ).fetchall()
        result = []
        for row in rows:
            members = db.execute(
                """
                SELECT u.id, u.username, COALESCE(p.display_name, u.username) AS display_name
                FROM conversation_members cm
                JOIN users u ON u.id = cm.user_id
                LEFT JOIN user_profiles p ON p.user_id = u.id
                WHERE cm.conversation_id = ? ORDER BY u.id
                """,
                (row["id"],),
            ).fetchall()
            result.append({
                "id": row["id"],
                "kind": row["kind"],
                "title": "ENORMOUS" if row["id"] == 1 else (row["title"] or ("Group chat" if row["kind"] == "group" else "Direct message")),
                "created_at": row["created_at"],
                "last_message_at": row["last_message_at"],
                "member_count": row["member_count"],
                "message_count": row["message_count"],
                "last_message": row["last_message"] or "",
                "members": [{"id": member["id"], "username": member["username"], "display_name": member["display_name"]} for member in members],
            })
        return jsonify(result)

    @app.route("/api/developer/conversations/<int:conversation_id>/messages", methods=["GET"])
    def api_developer_conversation_messages(conversation_id):
        _, denied = authorize_developer_api()
        if denied:
            return denied
        db = get_db()
        conversation = db.execute(
            "SELECT id, kind, title FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conversation is None:
            return jsonify({"error": "Conversation not found"}), 404

        members = db.execute(
            """
            SELECT u.id, u.username, COALESCE(p.display_name, u.username) AS display_name
            FROM conversation_members cm
            JOIN users u ON u.id = cm.user_id
            LEFT JOIN user_profiles p ON p.user_id = u.id
            WHERE cm.conversation_id = ? ORDER BY u.id
            """,
            (conversation_id,),
        ).fetchall()
        limit = max(1, min(request.args.get("limit", default=200, type=int), 200))
        messages = db.execute(
            """
            SELECT m.id, m.sender_id, m.text, m.created_at, m.attachment_name,
                   u.username, COALESCE(p.display_name, u.username) AS sender_name
            FROM conversation_messages m
            JOIN users u ON u.id = m.sender_id
            LEFT JOIN user_profiles p ON p.user_id = u.id
            WHERE m.conversation_id = ?
            ORDER BY m.id DESC LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
        member_payload = [{"id": row["id"], "username": row["username"], "display_name": row["display_name"]} for row in members]
        message_payload = []
        for message in reversed(messages):
            recipients = [member["display_name"] for member in member_payload if member["id"] != message["sender_id"]]
            message_payload.append({
                "id": message["id"],
                "sender_id": message["sender_id"],
                "sender": message["sender_name"],
                "recipients": recipients,
                "text": message["text"],
                "attachment_name": message["attachment_name"],
                "created_at": message["created_at"],
            })
        return jsonify({
            "conversation": {"id": conversation["id"], "kind": conversation["kind"], "title": conversation["title"]},
            "members": member_payload,
            "messages": message_payload,
            "limit": limit,
        })

    @app.route("/api/developer/events", methods=["GET"])
    def api_developer_events():
        _, denied = authorize_developer_api()
        if denied:
            return denied
        limit = max(1, min(request.args.get("limit", default=100, type=int), 200))
        rows = get_db().execute(
            "SELECT id, event_type, detail, endpoint, response_status, user_id, created_at FROM application_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.route("/api/developer/system", methods=["GET"])
    def api_developer_system():
        _, denied = authorize_developer_api()
        if denied:
            return denied
        db = get_db()
        table_names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        counts = {}
        for table_name in table_names:
            if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table_name):
                counts[table_name] = db.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
        return jsonify({
            "database_health": "ok" if integrity == "ok" else "needs attention",
            "table_counts": counts,
            "presence_sessions": counts.get("presence", 0),
            "presence_account_mapping": "not implemented",
            "ai_enabled": is_ai_enabled(),
            "debug_mode": bool(current_app.debug),
            "persistent_errors": "HTTP failures and selected app events only; inspect host logs for tracebacks",
        })

    @app.route("/robots.txt")
    def robots_txt():
        return "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/sitemap.xml")
    def sitemap_xml():
        xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
  <url><loc>https://example.com/</loc></url>
</urlset>
"""
        return xml, 200, {"Content-Type": "application/xml; charset=utf-8"}

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
                        created_user = register_user(username, password)
                        record_application_event("account_created", "Account registered", endpoint=request.endpoint, user_id=created_user["id"])
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
            ai_enabled=is_ai_enabled(),
        )

    @app.route("/logout", methods=["POST"])
    def logout_page():
        logout_user()
        return redirect(url_for("auth_page", mode="login"))

    @app.route("/profile", methods=["GET", "POST"])
    def profile_page():
        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_page", mode="login"))

        device_type = detect_device_type(request.user_agent.string)
        if request.method == "POST":
            profile = update_user_profile(
                current_user["id"],
                display_name=request.form.get("display_name"),
                bio=request.form.get("bio"),
                status=request.form.get("status"),
                avatar_url=request.form.get("avatar_url"),
            )
            return render_template(
                "profile.html",
                device_type=device_type,
                current_user=current_user,
                profile=profile,
                saved_message="Profile updated.",
            )

        profile = get_user_profile(current_user["id"])
        return render_template(
            "profile.html",
            device_type=device_type,
            current_user=current_user,
            profile=profile,
            saved_message=None,
        )

    @app.route("/profile/<username>", methods=["GET"])
    def public_profile_page(username):
        current_user = get_current_user()
        device_type = detect_device_type(request.user_agent.string)
        db = get_db()
        row = db.execute(
            "SELECT id, username FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return redirect(url_for("home"))

        profile = get_user_profile(row["id"])
        return render_template(
            "profile.html",
            device_type=device_type,
            current_user=current_user,
            profile=profile,
            saved_message=None,
            is_public=True,
        )

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        device_type = detect_device_type(request.user_agent.string)
        current_user = get_current_user()
        saved_message = None
        profile = get_user_profile(current_user["id"]) if current_user else None

        if request.method == "POST":
            if current_user is None:
                return redirect(url_for("auth_page", mode="login"))

            if "display_name" in request.form or "bio" in request.form or "status" in request.form:
                profile = update_user_profile(
                    current_user["id"],
                    display_name=request.form.get("display_name"),
                    bio=request.form.get("bio"),
                    status=request.form.get("status"),
                    avatar_url=request.form.get("avatar_url"),
                )
                saved_message = "Profile saved."
            else:
                if not is_ai_enabled():
                    saved_message = "Open.Chat AI is under development; settings were not saved."
                    settings_payload = get_user_idle_settings(current_user["id"])
                else:
                    idle_settings = save_user_idle_settings(
                        current_user["id"],
                        {
                            "name": request.form.get("chatbot_name"),
                            "personality": request.form.get("chatbot_personality"),
                            "greeting": request.form.get("chatbot_greeting"),
                        },
                    )
                    saved_message = "Idle AI preferences saved."
                    settings_payload = idle_settings

        if current_user is not None and request.method != "POST":
            settings_payload = get_user_idle_settings(current_user["id"])
        elif current_user is not None and request.method == "POST" and "display_name" not in request.form and "bio" not in request.form and "status" not in request.form:
            settings_payload = get_user_idle_settings(current_user["id"])
        else:
            settings_payload = get_user_idle_settings(current_user["id"] if current_user else None)

        return render_template(
            "settings.html",
            device_type=device_type,
            current_user=current_user,
            idle_settings=settings_payload,
            profile=profile,
            saved_message=saved_message,
            ai_enabled=is_ai_enabled(),
        )

    @app.route("/idle-ai", methods=["GET", "POST"])
    def idle_ai_page():
        current_user = get_current_user()
        if current_user is None:
            return redirect(url_for("auth_page", mode="login"))

        device_type = detect_device_type(request.user_agent.string)
        idle_settings = get_user_idle_settings(current_user["id"])
        history = session.get("idle_ai_history", [])
        ai_enabled = is_ai_enabled()

        if request.method == "POST":
            prompt = (request.form.get("message") or "").strip()
            if prompt and ai_enabled:
                history = history[-8:]
                history.append({"role": "user", "text": prompt})
                reply = generate_idle_ai_response(prompt, idle_settings, current_user["username"], history)
                history.append({"role": "ai", "text": reply})
                session["idle_ai_history"] = history

        return render_template(
            "idle_ai.html",
            device_type=device_type,
            current_user=current_user,
            idle_settings=idle_settings,
            history=session.get("idle_ai_history", []) or history,
            ai_enabled=ai_enabled,
        )

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

    @app.route("/api/users/search", methods=["GET"])
    def api_search_users():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        query = (request.args.get("q") or "").strip()
        return jsonify(search_users(query, current_user_id=current_user["id"]))

    @app.route("/api/conversations/start", methods=["POST"])
    def api_start_conversation():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        target_user_id = data.get("user_id")
        if target_user_id is None:
            return jsonify({"error": "Target user required"}), 400

        if isinstance(target_user_id, bool):
            return jsonify({"error": "Invalid target user"}), 400
        try:
            target_user_id = int(target_user_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid target user"}), 400

        try:
            conversation_id = start_conversation_for_users(current_user["id"], target_user_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        if conversation_id is None:
            return jsonify({"error": "Cannot start a chat with yourself"}), 400

        record_application_event("direct_conversation_opened", "Direct conversation opened or reused", endpoint=request.endpoint, user_id=current_user["id"])
        return jsonify({"conversation_id": conversation_id})

    @app.route("/api/groups/create", methods=["POST"])
    def api_create_group_conversation():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        title = (data.get("title") or "Group Chat").strip() or "Group Chat"
        member_ids = data.get("members") or []
        try:
            normalized_ids = [int(member_id) for member_id in member_ids if str(member_id).strip()]
        except (TypeError, ValueError):
            return jsonify({"error": "Group members must be numeric IDs."}), 400

        try:
            conversation_id = create_group_conversation(current_user["id"], title, normalized_ids)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        record_application_event("group_created", "Group conversation created", endpoint=request.endpoint, user_id=current_user["id"])
        return jsonify({"conversation_id": conversation_id, "title": title})

    @app.route("/api/conversations", methods=["GET"])
    def api_list_user_conversations():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        return jsonify(get_user_conversations(current_user["id"]))

    @app.route("/api/conversations/<int:conversation_id>/messages", methods=["GET"])
    def api_get_conversation_messages_route(conversation_id):
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        if conversation_id == 1:
            ensure_enormous_member(current_user["id"])

        db = get_db()
        member = db.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, current_user["id"]),
        ).fetchone()
        if member is None:
            return jsonify({"error": "Conversation not found"}), 404

        since_id = request.args.get("since_id", type=int)
        limit = request.args.get("limit", default=80, type=int)
        return jsonify(get_conversation_messages(conversation_id, user_id=current_user["id"], limit=limit, since_id=since_id))

    @app.route("/api/conversations/<int:conversation_id>/messages", methods=["POST"])
    def api_send_direct_message_route(conversation_id):
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        if conversation_id == 1:
            ensure_enormous_member(current_user["id"])

        db = get_db()
        member = db.execute(
            "SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, current_user["id"]),
        ).fetchone()
        if member is None:
            return jsonify({"error": "Unauthorized: Not a member of this conversation"}), 403

        attachment = request.files.get("attachment") if request.files else None
        attachment_name = None
        attachment_type = None
        attachment_url = None
        attachment_size = 0

        if attachment and attachment.filename:
            safe_name = secure_filename(attachment.filename)
            if safe_name:
                unique_name = f"{uuid.uuid4().hex}_{safe_name}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                attachment.save(save_path)
                attachment_name = safe_name
                attachment_type = attachment.mimetype or "application/octet-stream"
                attachment_url = url_for("static", filename=f"uploads/{unique_name}")
                attachment_size = os.path.getsize(save_path)

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}

        text = (data.get("text") or request.form.get("message") or "").strip()
        attachment_name = (data.get("attachment_name") or request.form.get("attachment_name") or attachment_name)
        attachment_type = (data.get("attachment_type") or request.form.get("attachment_type") or attachment_type)
        attachment_url = (data.get("attachment_url") or request.form.get("attachment_url") or attachment_url)
        attachment_size = data.get("attachment_size") if data.get("attachment_size") is not None else request.form.get("attachment_size")
        try:
            attachment_size = int(attachment_size or 0)
        except (TypeError, ValueError):
            attachment_size = 0

        try:
            payload = send_conversation_message(
                conversation_id,
                current_user["id"],
                text,
                attachment_name=attachment_name,
                attachment_type=attachment_type,
                attachment_url=attachment_url,
                attachment_size=attachment_size,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if payload is None:
            return jsonify({"error": "Message or attachment is required"}), 400

        record_application_event("conversation_message_sent", "Conversation message stored", endpoint=request.endpoint, user_id=current_user["id"])
        return jsonify(payload)

    @app.route("/api/calls/start", methods=["POST"])
    def api_start_call():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401
        return jsonify({"error": "Voice and video calls are under development", "feature_state": "under_development"}), 501

    @app.route("/api/calls/<int:call_id>/status", methods=["POST"])
    def api_update_call_status(call_id):
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401
        return jsonify({"error": "Voice and video calls are under development", "feature_state": "under_development"}), 501

    @app.route("/api/notifications", methods=["GET"])
    def api_notifications():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        unread_only = request.args.get("unread_only", "0").strip().lower() in {"1", "true", "yes"}
        limit = request.args.get("limit", default=25, type=int)
        return jsonify(get_user_notifications(current_user["id"], unread_only=unread_only, limit=max(1, min(limit, 100))))

    @app.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
    def api_notification_mark_read(notification_id):
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        return jsonify({"success": mark_notification_read(current_user["id"], notification_id)})

    @app.route("/api/conversations/<int:conversation_id>/read", methods=["POST"])
    def api_conversation_mark_read(conversation_id):
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        return jsonify({"success": mark_conversation_read(conversation_id, current_user["id"])})

    @app.route("/api/conversations/<int:conversation_id>/typing", methods=["POST"])
    def api_conversation_typing(conversation_id):
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        state = set_typing_state(conversation_id, current_user["id"], bool(data.get("is_typing", False)))
        return jsonify(state)

    @app.route("/api/conversations/<int:conversation_id>/typing", methods=["GET"])
    def api_get_conversation_typing(conversation_id):
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        db = get_db()
        rows = db.execute(
            "SELECT user_id, is_typing, updated_at FROM conversation_typing WHERE conversation_id = ? AND user_id != ?",
            (conversation_id, current_user["id"]),
        ).fetchall()

        typing_users = []
        for row in rows:
            if row["is_typing"]:
                typing_users.append({
                    "user_id": row["user_id"],
                    "is_typing": True,
                    "updated_at": row["updated_at"],
                })
        return jsonify({"typing": typing_users})

    @app.route("/api/statuses", methods=["GET", "POST"])
    def api_statuses():
        current_user = get_current_user()
        if current_user is None:
            return jsonify({"error": "Authentication required"}), 401

        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"error": "Request body must be a JSON object."}), 400

            text = (data.get("text") or "").strip()
            media_url = (data.get("media_url") or "").strip() or None
            try:
                status = save_user_status(current_user["id"], text, media_url=media_url)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify(status)

        return jsonify(get_user_statuses(current_user["id"]))

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

        attachment = request.files.get("attachment") if request.files else None
        attachment_name = None
        attachment_type = None
        attachment_url = None
        attachment_size = 0

        if attachment and attachment.filename:
            safe_name = secure_filename(attachment.filename)
            if safe_name:
                unique_name = f"{uuid.uuid4().hex}_{safe_name}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                attachment.save(save_path)
                attachment_name = safe_name
                attachment_type = attachment.mimetype or "application/octet-stream"
                attachment_url = url_for("static", filename=f"uploads/{unique_name}")
                attachment_size = os.path.getsize(save_path)

        if attachment is not None and not attachment.filename:
            attachment = None

        if request.is_json and not attachment:
            data = request.get_json(silent=True)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                return jsonify({"error": "Request body must be a JSON object."}), 400
            message_text = data.get("message")
            attachment_name = data.get("attachment_name") or attachment_name
            attachment_type = data.get("attachment_type") or attachment_type
            attachment_url = data.get("attachment_url") or attachment_url
            attachment_size = int(data.get("attachment_size") or attachment_size)
        else:
            message_text = request.form.get("message", "")

        payload = save_message(
            current_user["username"],
            message_text,
            attachment_name=attachment_name,
            attachment_type=attachment_type,
            attachment_url=attachment_url,
            attachment_size=attachment_size,
        )
        if payload is None:
            return jsonify({"error": "Message or attachment is required"}), 400
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
        ensure_profile_tables()
        ensure_conversation_message_tables()
        ensure_enormous_space()
        ensure_call_tables()
        ensure_notification_tables()
        ensure_application_event_table()
        migrate_legacy_messages()

    return app


app = create_app()


if __name__ == "__main__":
    debug_mode = str(os.getenv("FLASK_DEBUG", "false")).strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=debug_mode)