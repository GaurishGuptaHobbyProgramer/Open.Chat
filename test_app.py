import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import (
    add_group_member,
    authenticate_user,
    create_app,
    create_call_session,
    create_direct_chat,
    create_group_conversation,
    detect_device_type,
    format_message_time,
    get_ai_backend_settings,
    is_ai_enabled,
    get_conversation_messages,
    get_call_signals,
    get_db,
    get_messages,
    get_typing_state,
    get_user_conversations,
    get_user_notifications,
    get_user_statuses,
    mark_conversation_read,
    mark_notification_read,
    save_call_signal,
    set_typing_state,
    register_user,
    record_application_event,
    save_message,
    save_user_status,
    search_users,
    send_conversation_message,
    update_call_status,
    ensure_enormous_member,
    ensure_enormous_space,
    migrate_legacy_messages,
    PostgresConnection,
    PostgresCursor,
    PostgresRow,
    orm_db,
    _is_postgres_url,
    _normalize_postgres_url,
    _import_sqlite_data,
    update_user_profile,
)


class ChatDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.app = create_app({"TESTING": True, "DATABASE": self.db_path})

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_non_test_local_app_startup_does_not_modify_existing_database(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("CREATE TABLE preserved_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved_data VALUES ('keep')")
        connection.commit()
        connection.close()

        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            create_app({"DATABASE": self.db_path})

        connection = sqlite3.connect(self.db_path)
        self.assertEqual(connection.execute("SELECT value FROM preserved_data").fetchone()[0], "keep")
        connection.close()

    def test_production_requires_postgresql_database(self):
        with patch.dict(os.environ, {"APP_ENV": "development", "SECRET_KEY": "test-secret"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL must be a PostgreSQL"):
                create_app({
                    "APP_ENV": "production",
                    "SECRET_KEY": "test-secret",
                    "DATABASE": self.db_path,
                })

    def test_sqlite_import_rejects_source_without_users_table(self):
        source_fd, source_path = tempfile.mkstemp()
        os.close(source_fd)

        class FakeCursor:
            closed = False

            def close(self):
                self.closed = True

        class FakeConnection:
            def __init__(self):
                self.test_cursor = FakeCursor()
                self.rolled_back = False

            def cursor(self):
                return self.test_cursor

            def rollback(self):
                self.rolled_back = True

        target = FakeConnection()
        try:
            with self.assertRaisesRegex(ValueError, "required users table"):
                _import_sqlite_data(source_path, target, dry_run=True)
            self.assertTrue(target.rolled_back)
            self.assertTrue(target.test_cursor.closed)
        finally:
            os.remove(source_path)

    def test_postgresql_urls_require_tls_and_normalize_render_and_neon_schemes(self):
        pooled_url = _normalize_postgres_url("postgres://user:pass@neon-pooler.example/db")
        self.assertTrue(pooled_url.startswith("postgresql+psycopg://"))
        self.assertTrue(_is_postgres_url(pooled_url))
        self.assertIn("sslmode=require", pooled_url)
        psycopg_url = _normalize_postgres_url(
            "postgresql+psycopg://user:pass@neon.example/db?sslmode=require"
        )
        self.assertTrue(psycopg_url.startswith("postgresql+psycopg://"))
        self.assertTrue(_is_postgres_url(psycopg_url))
        psycopg2_url = _normalize_postgres_url(
            "postgresql+psycopg2://user:pass@neon.example/db?sslmode=require"
        )
        self.assertTrue(psycopg2_url.startswith("postgresql+psycopg://"))
        direct_url = _normalize_postgres_url(
            "postgresql://user:pass@neon.example/db?sslmode=verify-full"
        )
        self.assertTrue(direct_url.startswith("postgresql+psycopg://"))
        self.assertIn("sslmode=verify-full", direct_url)
        with self.assertRaisesRegex(ValueError, "must use TLS"):
            _normalize_postgres_url("postgresql://user:pass@neon.example/db?sslmode=disable")

    def test_postgres_row_supports_column_names_and_legacy_numeric_indexes(self):
        row = PostgresRow({"id": 42, "username": "Ada"}, ["id", "username"])
        self.assertEqual(row["id"], 42)
        self.assertEqual(row[0], 42)
        self.assertEqual(row[1], "Ada")

    def test_postgres_cursor_wraps_psycopg3_dictionary_rows(self):
        class FakeColumn:
            def __init__(self, name):
                self.name = name

        class FakeCursor:
            description = [FakeColumn("id"), FakeColumn("username")]

            def fetchone(self):
                return {"id": 42, "username": "Ada"}

        row = PostgresCursor(FakeCursor()).fetchone()
        self.assertEqual(row["username"], "Ada")
        self.assertEqual(row[0], 42)

    def test_flask_sqlalchemy_uses_psycopg3_for_render_postgres_urls(self):
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            with patch("app.get_db") as get_database:
                get_database.return_value.execute.return_value.fetchone.return_value = (1,)
                application = create_app({
                    "APP_ENV": "development",
                    "DATABASE_URL": "postgres://user:pass@localhost:5432/openchat",
                })

        self.assertTrue(application.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+psycopg://"))
        with application.app_context():
            self.assertEqual(orm_db.engine.dialect.driver, "psycopg")

    def test_postgresql_sqlite_compatibility_rewrites_insert_operations(self):
        class FakeCursor:
            rowcount = 1

            def __init__(self):
                self.statement = None
                self.parameters = None

            def execute(self, statement, parameters):
                self.statement = statement
                self.parameters = parameters

            def fetchone(self):
                return {"id": 42}

        class FakeConnection:
            def __init__(self):
                self.last_cursor = None
                self.row_factory = None

            def cursor(self, row_factory=None):
                self.last_cursor = FakeCursor()
                self.row_factory = row_factory
                return self.last_cursor

        connection = FakeConnection()
        database = PostgresConnection(connection)
        result = database.execute(
            "INSERT OR IGNORE INTO conversation_members "
            "(conversation_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (1, 7, "2026-01-01T00:00:00Z"),
        )
        self.assertIsNotNone(connection.row_factory)
        self.assertIn("ON CONFLICT DO NOTHING RETURNING id", connection.last_cursor.statement)
        self.assertIn("%s", connection.last_cursor.statement)
        self.assertEqual(connection.last_cursor.parameters, (1, 7, "2026-01-01T00:00:00Z"))
        self.assertEqual(result.lastrowid, 42)

        database.execute(
            "INSERT OR REPLACE INTO presence (token, created_at) VALUES (?, ?)",
            ("token", "2026-01-01T00:00:00Z"),
        )
        self.assertIn(
            "ON CONFLICT(token) DO UPDATE SET created_at = EXCLUDED.created_at RETURNING id",
            connection.last_cursor.statement,
        )

    def test_save_and_get_messages(self):
        with self.app.app_context():
            save_message("Alice", "Hello there")
            save_message("Bob", "Hi Alice")

            messages = get_messages()

            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["username"], "Alice")
            self.assertEqual(messages[0]["message"], "Hello there")
            self.assertEqual(messages[1]["username"], "Bob")
            self.assertEqual(messages[1]["message"], "Hi Alice")

    def test_message_time_format(self):
        self.assertIn("02/01/2025", format_message_time("2025-01-02T08:05:00Z"))

    def test_saved_message_uses_utc_iso_format(self):
        with self.app.app_context():
            payload = save_message("Alice", "Hello")
            self.assertIn("T", payload["created_at"])
            self.assertTrue(payload["created_at"].endswith("Z"))

    def test_auth_signup_and_login(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            user = register_user("Alice", "StrongPass123!")
            self.assertIsNotNone(user)
            self.assertEqual(user["username"], "1, Alice")
            self.assertTrue(authenticate_user("1, Alice", "StrongPass123!"))
            self.assertFalse(authenticate_user("1, Alice", "wrong-password"))

    def test_auth_routes_register_login_and_logout(self):
        client = self.app.test_client()
        signup = client.post(
            "/auth",
            data={
                "mode": "signup",
                "username": "Alice",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
            },
        )
        self.assertEqual(signup.status_code, 302)
        self.assertEqual(client.get("/messages").status_code, 200)

        logout = client.post("/logout")
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(client.get("/messages").status_code, 302)

        login = client.post(
            "/auth",
            data={"mode": "login", "username": "1, Alice", "password": "StrongPass123!"},
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(client.get("/messages").status_code, 200)

    def test_auth_pages_exist(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/auth").status_code, 200)
        settings = client.get("/settings")
        self.assertEqual(settings.status_code, 200)
        self.assertIn(b"OpenChat | Settings", settings.data)
        self.assertTrue(settings.data.strip())

    def test_secret_key_development_fallback_and_production_requirement(self):
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            os.environ.pop("SECRET_KEY", None)
            first_app = create_app({"TESTING": True, "DATABASE": self.db_path})
            second_app = create_app({"TESTING": True, "DATABASE": self.db_path})
            self.assertEqual(first_app.secret_key, second_app.secret_key)
            self.assertEqual(first_app.secret_key, "openchat-local-development-only")

        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            os.environ.pop("SECRET_KEY", None)
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY must be configured"):
                create_app({"TESTING": True, "DATABASE": self.db_path})

            os.environ["SECRET_KEY"] = "configured-test-secret"
            configured_app = create_app({"TESTING": True, "DATABASE": self.db_path})
            self.assertEqual(configured_app.secret_key, "configured-test-secret")

    def test_debug_mode_follows_environment_and_is_disabled_in_production(self):
        with patch.dict(os.environ, {"APP_ENV": "development", "FLASK_DEBUG": "1"}, clear=False):
            debug_app = create_app({"TESTING": True, "DATABASE": self.db_path})
            self.assertTrue(debug_app.debug)

        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "FLASK_DEBUG": "1", "SECRET_KEY": "configured-test-secret"},
            clear=False,
        ):
            production_app = create_app({"TESTING": True, "DATABASE": self.db_path, "DEBUG": True})
            self.assertFalse(production_app.debug)

    def test_production_warns_when_developer_id_is_missing_from_database(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "SECRET_KEY": "configured-test-secret"},
            clear=False,
        ):
            with self.assertLogs("app", level="WARNING") as captured:
                create_app({
                    "TESTING": True,
                    "DATABASE": self.db_path,
                    "OPENCHAT_DEVELOPER_USER_ID": "999999",
                })
        self.assertTrue(any("not present in the deployment database" in message for message in captured.output))

    def test_chat_requires_login(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.get("/messages").status_code, 302)

    def test_signup_allows_duplicate_names_and_generates_serial_usernames(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            first = register_user("Alice", "StrongPass123!")
            second = register_user("Alice", "AnotherPass456!")

            self.assertEqual(first["username"], "1, Alice")
            self.assertEqual(second["username"], "2, Alice")
            self.assertTrue(authenticate_user("1, Alice", "StrongPass123!"))
            self.assertTrue(authenticate_user("2, Alice", "AnotherPass456!"))

    def test_detect_device_type(self):
        self.assertEqual("mobile", detect_device_type("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"))
        self.assertEqual("mobile", detect_device_type("Mozilla/5.0 (Linux; Android 14; Pixel 8)"))
        self.assertEqual("desktop", detect_device_type("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))

    def test_ai_backend_uses_openrouter_free_model(self):
        original_key = os.environ.get("OPENROUTER_API_KEY")
        original_model = os.environ.get("OPENROUTER_MODEL")
        original_base_url = os.environ.get("OPENROUTER_BASE_URL")

        try:
            os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
            os.environ["OPENROUTER_MODEL"] = "openrouter/free"
            os.environ["OPENROUTER_BASE_URL"] = "https://openrouter.ai/api/v1"

            settings = get_ai_backend_settings()
            self.assertEqual(settings["api_key"], "test-openrouter-key")
            self.assertEqual(settings["model"], "openrouter/free")
            self.assertEqual(settings["base_url"], "https://openrouter.ai/api/v1")
            self.assertEqual(settings["openai_api_key"], "test-openrouter-key")
            self.assertEqual(settings["openai_model"], "openrouter/free")
            self.assertEqual(settings["openai_base_url"], "https://openrouter.ai/api/v1")
        finally:
            if original_key is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = original_key

            if original_model is None:
                os.environ.pop("OPENROUTER_MODEL", None)
            else:
                os.environ["OPENROUTER_MODEL"] = original_model

            if original_base_url is None:
                os.environ.pop("OPENROUTER_BASE_URL", None)
            else:
                os.environ["OPENROUTER_BASE_URL"] = original_base_url

    def test_ai_requires_explicit_enablement_and_api_key(self):
        original_enabled = os.environ.get("OPENCHAT_AI_ENABLED")
        original_key = os.environ.get("OPENROUTER_API_KEY")
        try:
            os.environ["OPENCHAT_AI_ENABLED"] = "true"
            os.environ.pop("OPENROUTER_API_KEY", None)
            self.assertFalse(is_ai_enabled())

            os.environ["OPENROUTER_API_KEY"] = "test-key"
            self.assertTrue(is_ai_enabled())

            os.environ["OPENCHAT_AI_ENABLED"] = "false"
            self.assertFalse(is_ai_enabled())
        finally:
            if original_enabled is None:
                os.environ.pop("OPENCHAT_AI_ENABLED", None)
            else:
                os.environ["OPENCHAT_AI_ENABLED"] = original_enabled
            if original_key is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = original_key

    def test_duplicate_names_are_allowed_and_serialized(self):
        duplicate_db_fd, duplicate_db_path = tempfile.mkstemp()
        os.close(duplicate_db_fd)

        try:
            conn = sqlite3.connect(duplicate_db_path)
            conn.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                ("Alice", "hash1", "2024-01-01T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                ("alice", "hash2", "2024-01-01T00:00:01Z"),
            )
            conn.commit()
            conn.close()

            app = create_app({"TESTING": True, "DATABASE": duplicate_db_path})
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
                usernames = [row[0] for row in db.execute("SELECT username FROM users ORDER BY id").fetchall()]
                self.assertIn("Alice", usernames)
                self.assertIn("alice", usernames)
        finally:
            if os.path.exists(duplicate_db_path):
                os.remove(duplicate_db_path)

    def test_message_can_store_attachment_metadata(self):
        with self.app.app_context():
            payload = save_message("Alice", "check this", attachment_name="notes.pdf", attachment_type="application/pdf")
            self.assertIn("attachment_name", payload)
            self.assertEqual(payload["attachment_name"], "notes.pdf")
            self.assertEqual(payload["attachment_type"], "application/pdf")

    def test_legacy_users_schema_is_migrated_to_serial_usernames(self):
        legacy_db_fd, legacy_db_path = tempfile.mkstemp()
        os.close(legacy_db_fd)

        try:
            conn = sqlite3.connect(legacy_db_path)
            conn.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL, email_verified INTEGER NOT NULL DEFAULT 0, otp_code TEXT, otp_expires_at TEXT)"
            )
            conn.execute(
                "INSERT INTO users (username, email, password_hash, created_at, email_verified) VALUES (?, ?, ?, ?, ?)",
                ("Akshay", "akshay@example.com", "hash1", "2024-01-01T00:00:00Z", 0),
            )
            conn.commit()
            conn.close()

            app = create_app({"TESTING": True, "DATABASE": legacy_db_path})
            with app.app_context():
                db = get_db()
                row = db.execute("SELECT username, password_hash FROM users ORDER BY id").fetchone()
                self.assertEqual(row[0], "1, Akshay")
                self.assertEqual(row[1], "hash1")
        finally:
            if os.path.exists(legacy_db_path):
                os.remove(legacy_db_path)

    def test_idle_ai_route_and_settings_personalization(self):
        client = self.app.test_client()

        with self.app.app_context(), self.app.test_request_context("/"):
            user = register_user("Alice", "StrongPass123!")
            user_id = user["id"]

        with client.session_transaction() as session:
            session["user_id"] = user_id

        get_response = client.get("/idle-ai")
        self.assertEqual(get_response.status_code, 200)
        self.assertIn("Idle AI from OpenChat", get_response.get_data(as_text=True))
        self.assertIn("Under Development — Coming Soon", get_response.get_data(as_text=True))
        self.assertNotIn("relaxed conversation, thoughtful company", get_response.get_data(as_text=True))
        self.assertIn("disabled", get_response.get_data(as_text=True))
        disabled_post = client.post("/idle-ai", data={"message": "Please reply"})
        self.assertEqual(disabled_post.status_code, 200)
        self.assertNotIn("Please reply", disabled_post.get_data(as_text=True))
        self.assertNotIn("relaxed conversation, thoughtful company", disabled_post.get_data(as_text=True))

        post_response = client.post(
            "/settings",
            data={
                "chatbot_name": "Alice's zen guide",
                "chatbot_personality": "calm and witty",
                "chatbot_greeting": "What shall we unpack today?",
            },
            follow_redirects=True,
        )
        self.assertEqual(post_response.status_code, 200)
        self.assertIn("settings were not saved", post_response.get_data(as_text=True))
        self.assertNotIn("Alice&#39;s zen guide", post_response.get_data(as_text=True))

    def test_user_profile_update_and_search(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")

            profile = update_user_profile(
                alice["id"],
                display_name="Alice Wonderland",
                bio="Designing calm products",
                status="Building",
            )
            self.assertEqual(profile["display_name"], "Alice Wonderland")
            self.assertEqual(profile["bio"], "Designing calm products")

            matches = search_users("alice", current_user_id=bob["id"])
            self.assertTrue(any(item["username"] == alice["username"] for item in matches))
            self.assertTrue(any(item["display_name"] == "Alice Wonderland" for item in matches))

    def test_user_search_matches_username_without_display_name_match(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            update_user_profile(alice["id"], display_name="Wonderland")

            matches = search_users(alice["username"], current_user_id=999)

            self.assertEqual([item["id"] for item in matches], [alice["id"]])

    def test_user_search_matches_display_name_without_username_match(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            update_user_profile(alice["id"], display_name="Wonderland")

            matches = search_users("Wonderland", current_user_id=999)

            self.assertEqual([item["id"] for item in matches], [alice["id"]])

    def test_user_search_excludes_current_user(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")

            matches = search_users(alice["username"], current_user_id=alice["id"])

            self.assertEqual(matches, [])

    def test_direct_chat_persistence_and_list(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")

            chat_id = create_direct_chat(alice["id"], bob["id"])
            send_conversation_message(chat_id, alice["id"], "Hi Bob")
            send_conversation_message(chat_id, bob["id"], "Hello Alice")

            messages = get_conversation_messages(chat_id, alice["id"])
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["sender_id"], alice["id"])
            self.assertEqual(messages[0]["text"], "Hi Bob")

            chats = get_user_conversations(alice["id"])
            self.assertTrue(any(item["conversation_id"] == chat_id for item in chats))
            self.assertTrue(any(item["unread_count"] >= 0 for item in chats))

    def test_direct_chat_routes_return_conversation_data(self):
        client = self.app.test_client()

        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            chat_id = create_direct_chat(alice["id"], bob["id"])

        with client.session_transaction() as session:
            session["user_id"] = alice["id"]

        list_response = client.get("/api/conversations")
        self.assertEqual(list_response.status_code, 200)
        conversations = list_response.get_json()
        self.assertTrue(any(item["conversation_id"] == chat_id for item in conversations))

        send_response = client.post(f"/api/conversations/{chat_id}/messages", json={"text": "Hi Bob"})
        self.assertEqual(send_response.status_code, 200)
        self.assertEqual(send_response.get_json()["text"], "Hi Bob")

        fetch_response = client.get(f"/api/conversations/{chat_id}/messages")
        self.assertEqual(fetch_response.status_code, 200)
        self.assertTrue(any(item["text"] == "Hi Bob" for item in fetch_response.get_json()))

    def test_group_chat_creation_and_membership(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            charlie = register_user("Charlie", "ThirdPass987!")

            group_id = create_group_conversation(alice["id"], "Launch Team", [bob["id"], charlie["id"]])
            self.assertIsNotNone(group_id)

            add_group_member(group_id, charlie["id"])
            send_conversation_message(group_id, bob["id"], "Ready to ship")

            group_conversations = get_user_conversations(alice["id"])
            self.assertTrue(any(item["conversation_id"] == group_id and item["kind"] == "group" for item in group_conversations))

            group_messages = get_conversation_messages(group_id, alice["id"])
            self.assertTrue(any(item["text"] == "Ready to ship" for item in group_messages))

    def test_conversation_messages_support_media_metadata(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            chat_id = create_direct_chat(alice["id"], bob["id"])

            payload = send_conversation_message(
                chat_id,
                alice["id"],
                "Shared a note",
                attachment_name="brief.pdf",
                attachment_type="application/pdf",
                attachment_url="/static/uploads/brief.pdf",
                attachment_size=256,
            )

            self.assertEqual(payload["attachment_name"], "brief.pdf")
            self.assertEqual(payload["attachment_type"], "application/pdf")
            self.assertEqual(payload["attachment_url"], "/static/uploads/brief.pdf")

            messages = get_conversation_messages(chat_id, alice["id"])
            self.assertTrue(any(item["attachment_name"] == "brief.pdf" for item in messages))

    def test_user_statuses_can_be_saved_and_read(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            status = save_user_status(alice["id"], "Launching Open.Chat beta", media_url="/static/uploads/status.jpg")

            self.assertEqual(status["text"], "Launching Open.Chat beta")
            self.assertEqual(status["media_url"], "/static/uploads/status.jpg")

            statuses = get_user_statuses(alice["id"])
            self.assertTrue(any(item["text"] == "Launching Open.Chat beta" for item in statuses))

    def test_voice_message_attachment_and_call_session(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            chat_id = create_direct_chat(alice["id"], bob["id"])

            voice = send_conversation_message(
                chat_id,
                alice["id"],
                "",
                attachment_name="voice.ogg",
                attachment_type="audio/ogg",
                attachment_url="/static/uploads/voice.ogg",
                attachment_size=900,
            )
            self.assertEqual(voice["attachment_type"], "audio/ogg")
            self.assertIn("voice.ogg", voice["attachment_name"])

            call = create_call_session(alice["id"], bob["id"], "audio", chat_id)
            self.assertEqual(call["status"], "ringing")
            self.assertEqual(call["conversation_id"], chat_id)

    def test_call_signaling_messages_are_stored_and_retrieved(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            chat_id = create_direct_chat(alice["id"], bob["id"])
            call = create_call_session(alice["id"], bob["id"], "video", chat_id)

            signal = save_call_signal(call["id"], alice["id"], bob["id"], "offer", {"sdp": "offer-sdp"})

            self.assertGreater(signal["id"], 0)
            self.assertEqual(get_call_signals(call["id"], bob["id"])[0]["payload"]["sdp"], "offer-sdp")
            self.assertEqual(get_call_signals(call["id"], alice["id"]), [])

    def test_participant_cannot_join_overlapping_calls(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            carol = register_user("Carol", "ThirdPass789!")
            alice_bob_chat = create_direct_chat(alice["id"], bob["id"])
            bob_carol_chat = create_direct_chat(bob["id"], carol["id"])
            create_call_session(alice["id"], bob["id"], "audio", alice_bob_chat)

            with self.assertRaisesRegex(ValueError, "already in an active call"):
                create_call_session(bob["id"], carol["id"], "audio", bob_carol_chat)

    def test_call_api_starts_and_answers_authenticated_calls(self):
        alice_client = self.app.test_client()
        bob_client = self.app.test_client()
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            chat_id = create_direct_chat(alice["id"], bob["id"])

        with alice_client.session_transaction() as session:
            session["user_id"] = alice["id"]
        with bob_client.session_transaction() as session:
            session["user_id"] = bob["id"]

        start = alice_client.post("/api/calls/start", json={
            "target_user_id": bob["id"],
            "conversation_id": chat_id,
            "mode": "audio",
        })
        call = start.get_json()
        self.assertEqual(start.status_code, 201)
        self.assertEqual(call["mode"], "audio")

        signal = alice_client.post(
            f"/api/calls/{call['id']}/signals",
            json={"signal_type": "offer", "payload": {"sdp": "offer-sdp"}},
        )
        received = bob_client.get(f"/api/calls/{call['id']}/signals").get_json()
        accepted = bob_client.post(f"/api/calls/{call['id']}/status", json={"status": "accepted"})
        answer = bob_client.post(
            f"/api/calls/{call['id']}/signals",
            json={"signal_type": "answer", "payload": {"sdp": "answer-sdp"}},
        )
        candidate = alice_client.post(
            f"/api/calls/{call['id']}/signals",
            json={"signal_type": "ice-candidate", "payload": {"candidate": "candidate"}},
        )
        caller_signals = alice_client.get(f"/api/calls/{call['id']}/signals").get_json()
        callee_signals = bob_client.get(
            f"/api/calls/{call['id']}/signals?after_id={received[0]['id']}"
        ).get_json()
        rejected_after_answer = bob_client.post(
            f"/api/calls/{call['id']}/status", json={"status": "rejected"}
        )
        ended = bob_client.post(f"/api/calls/{call['id']}/status", json={"status": "ended"})

        self.assertEqual(signal.status_code, 201)
        self.assertEqual(received[0]["payload"]["sdp"], "offer-sdp")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(caller_signals[0]["payload"]["sdp"], "answer-sdp")
        self.assertEqual(candidate.status_code, 201)
        self.assertEqual(callee_signals[0]["signal_type"], "ice-candidate")
        self.assertEqual(rejected_after_answer.status_code, 409)
        self.assertEqual(ended.status_code, 200)
        self.assertEqual(bob_client.get(f"/api/calls/{call['id']}").get_json()["status"], "ended")

        rejected_call = alice_client.post("/api/calls/start", json={
            "target_user_id": bob["id"],
            "conversation_id": chat_id,
            "mode": "audio",
        }).get_json()
        rejected = bob_client.post(
            f"/api/calls/{rejected_call['id']}/status", json={"status": "rejected"}
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(alice_client.get(f"/api/calls/{rejected_call['id']}").get_json()["status"], "rejected")

        cancelled_call = alice_client.post("/api/calls/start", json={
            "target_user_id": bob["id"],
            "conversation_id": chat_id,
            "mode": "audio",
        }).get_json()
        cancelled = alice_client.post(
            f"/api/calls/{cancelled_call['id']}/status", json={"status": "cancelled"}
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(bob_client.get(f"/api/calls/{cancelled_call['id']}").get_json()["status"], "cancelled")

    def test_notifications_are_created_and_can_be_marked_read(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            chat_id = create_direct_chat(alice["id"], bob["id"])

            message = send_conversation_message(chat_id, alice["id"], "Morning Bob")
            self.assertIsNotNone(message)

            notifications = get_user_notifications(bob["id"])
            self.assertTrue(any(item["body"] == "Morning Bob" for item in notifications))

            marked = mark_notification_read(bob["id"], notifications[0]["id"])
            self.assertTrue(marked)
            unread = get_user_notifications(bob["id"], unread_only=True)
            self.assertFalse(any(item["id"] == notifications[0]["id"] for item in unread))

    def test_read_receipts_and_typing_state_are_tracked(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            chat_id = create_direct_chat(alice["id"], bob["id"])

            send_conversation_message(chat_id, alice["id"], "Hi there")
            self.assertTrue(mark_conversation_read(chat_id, bob["id"]))

            typing = set_typing_state(chat_id, bob["id"], True)
            self.assertTrue(typing["is_typing"])
            self.assertEqual(get_typing_state(chat_id, bob["id"])["user_id"], bob["id"])

            typing = set_typing_state(chat_id, bob["id"], False)
            self.assertFalse(typing["is_typing"])
            self.assertFalse(get_typing_state(chat_id, bob["id"])["is_typing"])


class CanonicalMessagingTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.app = create_app({"TESTING": True, "DATABASE": self.db_path})
        self.client = self.app.test_client()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_start_dm_creates_exact_members_lists_profile_and_isolates_messages(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            charlie = register_user("Charlie", "CharliePass789!")
            update_user_profile(bob["id"], display_name="Bobby", avatar_url="/static/uploads/bob.png")

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]

        response = self.client.post("/api/conversations/start", json={"user_id": bob["id"]})
        self.assertEqual(response.status_code, 200)
        dm_id = response.get_json()["conversation_id"]
        self.assertNotEqual(dm_id, 1)

        with self.app.app_context():
            db = get_db()
            member_ids = {
                row["user_id"]
                for row in db.execute(
                    "SELECT user_id FROM conversation_members WHERE conversation_id = ?",
                    (dm_id,),
                ).fetchall()
            }
            self.assertEqual(member_ids, {alice["id"], bob["id"]})

        other_dm_response = self.client.post("/api/conversations/start", json={"user_id": charlie["id"]})
        self.assertEqual(other_dm_response.status_code, 200)
        other_dm_id = other_dm_response.get_json()["conversation_id"]
        self.assertNotEqual(dm_id, other_dm_id)

        listed = self.client.get("/api/conversations")
        self.assertEqual(listed.status_code, 200)
        bob_conversation = next(item for item in listed.get_json() if item["conversation_id"] == dm_id)
        self.assertEqual(bob_conversation["title"], "Bobby")
        self.assertEqual(bob_conversation["avatar_url"], "/static/uploads/bob.png")
        self.assertEqual(bob_conversation["other_user_id"], bob["id"])

        self.assertEqual(self.client.post("/api/conversations/1/messages", json={"text": "enormous only"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/conversations/{dm_id}/messages", json={"text": "Bob only"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/conversations/{other_dm_id}/messages", json={"text": "Charlie only"}).status_code, 200)

        bob_messages = self.client.get(f"/api/conversations/{dm_id}/messages").get_json()
        enormous_messages = self.client.get("/api/conversations/1/messages").get_json()
        charlie_messages = self.client.get(f"/api/conversations/{other_dm_id}/messages").get_json()
        self.assertIn("Bob only", [item["text"] for item in bob_messages])
        self.assertNotIn("enormous only", [item["text"] for item in bob_messages])
        self.assertNotIn("Charlie only", [item["text"] for item in bob_messages])
        self.assertIn("enormous only", [item["text"] for item in enormous_messages])
        self.assertNotIn("Bob only", [item["text"] for item in enormous_messages])
        self.assertIn("Charlie only", [item["text"] for item in charlie_messages])
        self.assertNotIn("Bob only", [item["text"] for item in charlie_messages])

    def test_start_dm_reuses_existing_conversation_and_repeated_requests(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            existing_id = create_direct_chat(alice["id"], bob["id"])

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]

        first = self.client.post("/api/conversations/start", json={"user_id": bob["id"]})
        second = self.client.post("/api/conversations/start", json={"user_id": bob["id"]})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["conversation_id"], existing_id)
        self.assertEqual(second.get_json()["conversation_id"], existing_id)
        self.assertEqual(self.client.get(f"/api/conversations/{existing_id}/messages").status_code, 200)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations WHERE kind = 'direct'").fetchone()[0], 1)

    def test_start_dm_requires_authentication_rejects_self_and_missing_user(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")

        unauthorized = self.client.post("/api/conversations/start", json={"user_id": alice["id"]})
        self.assertEqual(unauthorized.status_code, 401)

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]

        self.assertEqual(self.client.post("/api/conversations/start", json={"user_id": alice["id"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/conversations/start", json={"user_id": 9999}).status_code, 404)

    def test_nonmember_cannot_access_dm_created_by_start_workflow(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            charlie = register_user("Charlie", "CharliePass789!")

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]
        response = self.client.post("/api/conversations/start", json={"user_id": bob["id"]})
        dm_id = response.get_json()["conversation_id"]

        with self.client.session_transaction() as session:
            session["user_id"] = charlie["id"]

        self.assertEqual(self.client.get(f"/api/conversations/{dm_id}/messages").status_code, 404)
        self.assertEqual(self.client.post(f"/api/conversations/{dm_id}/messages", json={"text": "intrusion"}).status_code, 403)

    def test_enormous_messages_stored_in_conversation_messages_and_isolated(self):
        """Test ENORMOUS: opened as conversation 1, messages saved to conversation_messages, isolated from DMs."""
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            dm_id = create_direct_chat(alice["id"], bob["id"])

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]

        # 1. Send message to ENORMOUS (conversation ID 1)
        res = self.client.post("/api/conversations/1/messages", json={"text": "Hello ENORMOUS community!"})
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["conversation_id"], 1)
        self.assertEqual(payload["text"], "Hello ENORMOUS community!")

        # 2. Confirm stored in conversation_messages table under conversation_id = 1
        with self.app.app_context():
            db = get_db()
            row = db.execute("SELECT * FROM conversation_messages WHERE conversation_id = 1 AND text = ?", ("Hello ENORMOUS community!",)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["conversation_id"], 1)

        # 3. Retrieve ENORMOUS messages via endpoint
        enormous_res = self.client.get("/api/conversations/1/messages")
        self.assertEqual(enormous_res.status_code, 200)
        enormous_messages = enormous_res.get_json()
        self.assertTrue(any(m["text"] == "Hello ENORMOUS community!" for m in enormous_messages))

        # 4. Confirm ENORMOUS message does NOT appear in Alice and Bob's DM
        dm_res = self.client.get(f"/api/conversations/{dm_id}/messages")
        self.assertEqual(dm_res.status_code, 200)
        dm_messages = dm_res.get_json()
        self.assertFalse(any(m["text"] == "Hello ENORMOUS community!" for m in dm_messages))

    def test_personal_chat_isolation(self):
        """Test Personal Chat: messages only in that DM, not in ENORMOUS, and persists across switches."""
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            dm_id = create_direct_chat(alice["id"], bob["id"])

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]

        # Alice sends private message in DM
        dm_send = self.client.post(f"/api/conversations/{dm_id}/messages", json={"text": "Top secret DM message"})
        self.assertEqual(dm_send.status_code, 200)
        self.assertEqual(dm_send.get_json()["conversation_id"], dm_id)

        # Check DM messages
        dm_fetch = self.client.get(f"/api/conversations/{dm_id}/messages")
        self.assertTrue(any(m["text"] == "Top secret DM message" for m in dm_fetch.get_json()))

        # Switch to ENORMOUS: confirm DM message does NOT appear there
        enormous_fetch = self.client.get("/api/conversations/1/messages")
        self.assertFalse(any(m["text"] == "Top secret DM message" for m in enormous_fetch.get_json()))

        # Switch back to DM: confirm DM message is still there
        dm_fetch_again = self.client.get(f"/api/conversations/{dm_id}/messages")
        self.assertTrue(any(m["text"] == "Top secret DM message" for m in dm_fetch_again.get_json()))

    def test_group_chat_isolation(self):
        """Test Group Chat: has its own ID, members, messages in conversation_messages, isolated from DMs and ENORMOUS."""
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            charlie = register_user("Charlie", "CharliePass789!")
            dm_id = create_direct_chat(alice["id"], bob["id"])
            group_id = create_group_conversation(alice["id"], "Secret Project", [bob["id"], charlie["id"]])

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]

        # Alice sends message in group
        grp_send = self.client.post(f"/api/conversations/{group_id}/messages", json={"text": "Project roadmap meeting at 3pm"})
        self.assertEqual(grp_send.status_code, 200)

        # Only that group's messages contain this
        grp_fetch = self.client.get(f"/api/conversations/{group_id}/messages")
        self.assertTrue(any(m["text"] == "Project roadmap meeting at 3pm" for m in grp_fetch.get_json()))

        # DM does not contain this
        dm_fetch = self.client.get(f"/api/conversations/{dm_id}/messages")
        self.assertFalse(any(m["text"] == "Project roadmap meeting at 3pm" for m in dm_fetch.get_json()))

        # ENORMOUS does not contain this
        enormous_fetch = self.client.get("/api/conversations/1/messages")
        self.assertFalse(any(m["text"] == "Project roadmap meeting at 3pm" for m in enormous_fetch.get_json()))

    def test_conversation_switching_flow(self):
        """Test Switching: ENORMOUS -> DM -> Group -> ENORMOUS -> DM: messages from other conversations never leak."""
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            dm_id = create_direct_chat(alice["id"], bob["id"])
            group_id = create_group_conversation(alice["id"], "Design Team", [bob["id"]])

            send_conversation_message(1, alice["id"], "Msg in ENORMOUS")
            send_conversation_message(dm_id, alice["id"], "Msg in DM")
            send_conversation_message(group_id, alice["id"], "Msg in Group")

        with self.client.session_transaction() as session:
            session["user_id"] = alice["id"]

        # 1. ENORMOUS
        e1 = [m["text"] for m in self.client.get("/api/conversations/1/messages").get_json()]
        self.assertIn("Msg in ENORMOUS", e1)
        self.assertNotIn("Msg in DM", e1)
        self.assertNotIn("Msg in Group", e1)

        # 2. DM
        d1 = [m["text"] for m in self.client.get(f"/api/conversations/{dm_id}/messages").get_json()]
        self.assertNotIn("Msg in ENORMOUS", d1)
        self.assertIn("Msg in DM", d1)
        self.assertNotIn("Msg in Group", d1)

        # 3. Group
        g1 = [m["text"] for m in self.client.get(f"/api/conversations/{group_id}/messages").get_json()]
        self.assertNotIn("Msg in ENORMOUS", g1)
        self.assertNotIn("Msg in DM", g1)
        self.assertIn("Msg in Group", g1)

        # 4. ENORMOUS again
        e2 = [m["text"] for m in self.client.get("/api/conversations/1/messages").get_json()]
        self.assertIn("Msg in ENORMOUS", e2)
        self.assertNotIn("Msg in DM", e2)
        self.assertNotIn("Msg in Group", e2)

        # 5. DM again
        d2 = [m["text"] for m in self.client.get(f"/api/conversations/{dm_id}/messages").get_json()]
        self.assertNotIn("Msg in ENORMOUS", d2)
        self.assertIn("Msg in DM", d2)
        self.assertNotIn("Msg in Group", d2)

    def test_security_non_member_cannot_access_or_send(self):
        """Test Security: user cannot retrieve or send messages to a conversation they are not a member of."""
        with self.app.app_context(), self.app.test_request_context("/"):
            alice = register_user("Alice", "StrongPass123!")
            bob = register_user("Bob", "AnotherPass456!")
            charlie = register_user("Charlie", "CharliePass789!")
            dm_id = create_direct_chat(alice["id"], bob["id"])
            send_conversation_message(dm_id, alice["id"], "Alice and Bob only")

        # Charlie logs in
        with self.client.session_transaction() as session:
            session["user_id"] = charlie["id"]

        # Charlie tries to GET Alice and Bob's DM
        get_res = self.client.get(f"/api/conversations/{dm_id}/messages")
        self.assertEqual(get_res.status_code, 404)

        # Charlie tries to POST into Alice and Bob's DM
        post_res = self.client.post(f"/api/conversations/{dm_id}/messages", json={"text": "I am an intruder"})
        self.assertEqual(post_res.status_code, 403)

        # Confirm intruder message was NOT saved
        with self.app.app_context():
            db = get_db()
            row = db.execute("SELECT 1 FROM conversation_messages WHERE conversation_id = ? AND text = ?", (dm_id, "I am an intruder")).fetchone()
            self.assertIsNone(row)

    def test_legacy_messages_safe_migration_and_preservation(self):
        """Test Backward Compatibility: old messages are migrated into ENORMOUS (convo 1), messages table preserved."""
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO messages (username, message, created_at) VALUES (?, ?, ?)",
                ("LegacyUser", "Old historical global message", "2025-01-01T10:00:00Z"),
            )
            db.commit()

            migrate_legacy_messages(db)

            cm_row = db.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id = 1 AND text = ?",
                ("Old historical global message",),
            ).fetchone()
            self.assertIsNotNone(cm_row)
            self.assertEqual(cm_row["conversation_id"], 1)

            old_row = db.execute(
                "SELECT * FROM messages WHERE message = ?",
                ("Old historical global message",),
            ).fetchone()
            self.assertIsNotNone(old_row)

            count_before = db.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = 1").fetchone()[0]
            migrate_legacy_messages(db)
            count_after = db.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = 1").fetchone()[0]
            self.assertEqual(count_before, count_after)


class DeveloperControlCenterTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.app = create_app({"TESTING": True, "DATABASE": self.db_path})
        self.client = self.app.test_client()

        with self.app.app_context(), self.app.test_request_context("/"):
            self.developer = register_user("Configured Developer", "DeveloperPass123!")
            self.alice = register_user("Alice", "StrongPass123!")
            self.bob = register_user("Bob", "AnotherPass456!")
            self.dm_id = create_direct_chat(self.alice["id"], self.bob["id"])
            self.group_id = create_group_conversation(self.alice["id"], "Review Group", [self.bob["id"]])
            send_conversation_message(1, self.alice["id"], "ENORMOUS operations message")
            send_conversation_message(self.dm_id, self.alice["id"], "DM operations message")
            send_conversation_message(self.group_id, self.bob["id"], "Group operations message")

        self.app.config["OPENCHAT_DEVELOPER_USER_ID"] = self.developer["id"]

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_unauthenticated_and_normal_users_are_denied_every_developer_surface(self):
        self.assertEqual(self.client.get("/developer").status_code, 302)
        self.assertEqual(self.client.get("/api/developer/users").status_code, 401)

        with self.client.session_transaction() as session:
            session["user_id"] = self.alice["id"]

        self.assertEqual(self.client.get("/developer").status_code, 403)
        endpoints = [
            ("GET", "/api/developer/users"),
            ("GET", f"/api/developer/users?user_id={self.developer['id']}"),
            ("GET", f"/api/developer/users/{self.bob['id']}"),
            ("POST", f"/api/developer/users/{self.bob['id']}/suspension"),
            ("GET", "/api/developer/conversations"),
            ("GET", f"/api/developer/conversations/{self.dm_id}/messages"),
            ("GET", "/api/developer/events"),
            ("GET", "/api/developer/system"),
        ]
        for method, endpoint in endpoints:
            with self.subTest(method=method, endpoint=endpoint):
                response = self.client.open(endpoint, method=method, json={"suspended": True} if method == "POST" else None)
                self.assertEqual(response.status_code, 403)

    def test_configured_developer_can_inspect_operations_without_secrets(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.developer["id"]

        page = self.client.get("/developer")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"does not currently provide end-to-end encryption", page.data)

        users = self.client.get("/api/developer/users")
        self.assertEqual(users.status_code, 200)
        user_records = users.get_json()
        selected_user = self.client.get(f"/api/developer/users/{self.alice['id']}")
        self.assertEqual(selected_user.status_code, 200)
        self.assertEqual(selected_user.get_json()["id"], self.alice["id"])
        self.assertNotIn("password_hash", str(selected_user.get_json()))
        self.assertTrue(any(user["id"] == self.alice["id"] for user in user_records))
        developer_record = next(user for user in user_records if user["id"] == self.developer["id"])
        alice_record = next(user for user in user_records if user["id"] == self.alice["id"])
        self.assertTrue(developer_record["is_developer"])
        self.assertFalse(alice_record["is_developer"])
        self.assertEqual(alice_record["account_status"], "Active")
        self.assertFalse(alice_record["is_suspended"])
        self.assertEqual(alice_record["online_status"], "Not Tracked")
        self.assertEqual(alice_record["last_activity"], "Not Tracked")
        self.assertNotIn("password_hash", str(user_records))
        self.assertNotIn("password", str(user_records).lower())
        for forbidden in ("session_token", "api_key", "SECRET_KEY", "Authorization", "Cookie"):
            self.assertNotIn(forbidden.lower(), str(user_records).lower())

        conversations = self.client.get("/api/developer/conversations")
        self.assertEqual(conversations.status_code, 200)
        records = conversations.get_json()
        self.assertTrue(any(item["id"] == 1 and item["title"] == "ENORMOUS" for item in records))
        self.assertTrue(any(item["id"] == self.group_id and item["kind"] == "group" for item in records))

        dm_messages = self.client.get(f"/api/developer/conversations/{self.dm_id}/messages")
        self.assertEqual(dm_messages.status_code, 200)
        dm_payload = dm_messages.get_json()
        dm_message = next(item for item in dm_payload["messages"] if item["text"] == "DM operations message")
        self.assertEqual(dm_message["sender"], self.alice["username"])
        self.assertEqual(dm_message["recipients"], [self.bob["username"]])
        self.assertNotIn("ENORMOUS operations message", str(dm_payload))
        self.assertEqual(self.client.get("/api/developer/conversations/999999/messages").status_code, 404)

        enormous = self.client.get("/api/developer/conversations/1/messages")
        self.assertEqual(enormous.status_code, 200)
        self.assertTrue(any(item["text"] == "ENORMOUS operations message" for item in enormous.get_json()["messages"]))

        group_messages = self.client.get(f"/api/developer/conversations/{self.group_id}/messages")
        self.assertEqual(group_messages.status_code, 200)
        group_payload = group_messages.get_json()
        self.assertEqual({member["id"] for member in group_payload["members"]}, {self.alice["id"], self.bob["id"]})
        group_message = next(item for item in group_payload["messages"] if item["text"] == "Group operations message")
        self.assertEqual(group_message["sender"], self.bob["username"])
        self.assertEqual(group_message["recipients"], [self.alice["username"]])

        system = self.client.get("/api/developer/system")
        self.assertEqual(system.status_code, 200)
        self.assertEqual(system.get_json()["database_health"], "ok")
        self.assertEqual(system.get_json()["debug_mode"], self.app.debug)
        self.assertEqual(system.get_json()["presence_account_mapping"], "not implemented")
        self.assertNotIn("SECRET_KEY", str(system.get_json()))
        self.assertNotIn("password_hash", str(system.get_json()))
        self.assertNotIn("api_key", str(system.get_json()).lower())

        self.client.post("/api/conversations/start", json={"user_id": self.bob["id"]})
        events = self.client.get("/api/developer/events").get_json()
        self.assertTrue(any(item["event_type"] == "direct_conversation_opened" for item in events))
        self.assertTrue(any(item["event_type"] == "failed_request" for item in events))

    def test_developer_can_suspend_and_reactivate_accounts(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.developer["id"]

        alice_client = self.app.test_client()
        with alice_client.session_transaction() as session:
            session["user_id"] = self.alice["id"]

        invalid_payload = self.client.post(
            f"/api/developer/users/{self.alice['id']}/suspension",
            json={"suspended": "yes"},
        )
        self.assertEqual(invalid_payload.status_code, 400)
        self.assertEqual(
            self.client.post("/api/developer/users/999999/suspension", json={"suspended": True}).status_code,
            404,
        )

        suspended = self.client.post(
            f"/api/developer/users/{self.alice['id']}/suspension",
            json={"suspended": True},
        )
        self.assertEqual(suspended.status_code, 200)
        self.assertTrue(suspended.get_json()["is_suspended"])

        accounts = self.client.get("/api/developer/users").get_json()
        alice_record = next(user for user in accounts if user["id"] == self.alice["id"])
        self.assertEqual(alice_record["account_status"], "Suspended")
        self.assertTrue(alice_record["is_suspended"])

        self.assertEqual(alice_client.get("/").status_code, 302)
        with alice_client.session_transaction() as session:
            self.assertNotIn("user_id", session)

        login = alice_client.post(
            "/auth",
            data={"mode": "login", "username": self.alice["username"], "password": "StrongPass123!"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn(b"Invalid username or password", login.data)

        reactivated = self.client.post(
            f"/api/developer/users/{self.alice['id']}/suspension",
            json={"suspended": False},
        )
        self.assertEqual(reactivated.status_code, 200)
        self.assertFalse(reactivated.get_json()["is_suspended"])
        login = alice_client.post(
            "/auth",
            data={"mode": "login", "username": self.alice["username"], "password": "StrongPass123!"},
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.headers["Location"], "/")

        events = self.client.get("/api/developer/events").get_json()
        self.assertTrue(any(event["event_type"] == "account_suspended" for event in events))
        self.assertTrue(any(event["event_type"] == "account_reactivated" for event in events))

    def test_developer_accounts_cannot_be_suspended(self):
        self.app.config["OPENCHAT_DEVELOPER_USER_ID"] = f"{self.developer['id']},{self.bob['id']}"
        with self.client.session_transaction() as session:
            session["user_id"] = self.developer["id"]
        for user_id in (self.developer["id"], self.bob["id"]):
            with self.subTest(user_id=user_id):
                response = self.client.post(
                    f"/api/developer/users/{user_id}/suspension",
                    json={"suspended": True},
                )
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()["error"], "Developer accounts cannot be suspended.")

    def test_developer_login_opens_control_center_and_regular_login_opens_chat(self):
        developer_client = self.app.test_client()
        developer_response = developer_client.post(
            "/auth",
            data={
                "mode": "login",
                "username": self.developer["username"],
                "password": "DeveloperPass123!",
            },
        )
        self.assertEqual(developer_response.status_code, 302)
        self.assertEqual(developer_response.headers["Location"], "/developer")
        developer_page = developer_client.get(developer_response.headers["Location"])
        self.assertEqual(developer_page.status_code, 200)
        self.assertIn(b"Developer Control Center", developer_page.data)

        regular_client = self.app.test_client()
        regular_response = regular_client.post(
            "/auth",
            data={
                "mode": "login",
                "username": self.alice["username"],
                "password": "StrongPass123!",
            },
        )
        self.assertEqual(regular_response.status_code, 302)
        self.assertEqual(regular_response.headers["Location"], "/")
        home_page = regular_client.get(regular_response.headers["Location"])
        self.assertEqual(home_page.status_code, 200)
        self.assertIn(b"Open.Chat | Communication", home_page.data)

    def test_developer_id_mismatch_is_denied_and_events_are_bounded(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.alice["id"]

        self.app.config["OPENCHAT_DEVELOPER_USER_ID"] = self.bob["id"]
        self.assertEqual(self.client.get("/api/developer/users").status_code, 403)

        with self.app.app_context():
            for index in range(1002):
                record_application_event("audit_test", f"event {index}")
            db = get_db()
            count = db.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
            self.assertEqual(count, 1000)
            details = " ".join(row[0] for row in db.execute("SELECT detail FROM application_events").fetchall())
            self.assertNotIn("session-cookie-value", details)
            self.assertNotIn("request-body-secret", details)

    def test_multiple_configured_developers_have_control_center_access(self):
        self.app.config["OPENCHAT_DEVELOPER_USER_ID"] = f"{self.developer['id']}, {self.bob['id']}"
        with self.client.session_transaction() as session:
            session["user_id"] = self.bob["id"]

        self.assertEqual(self.client.get("/developer").status_code, 200)
        users = self.client.get("/api/developer/users")
        self.assertEqual(users.status_code, 200)
        records = {user["id"]: user for user in users.get_json()}
        self.assertTrue(records[self.developer["id"]]["is_developer"])
        self.assertTrue(records[self.bob["id"]]["is_developer"])
        self.assertFalse(records[self.alice["id"]]["is_developer"])


if __name__ == "__main__":
    unittest.main()
