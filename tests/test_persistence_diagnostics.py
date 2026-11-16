import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch

from app import (
    _postgres_database_identity,
    _postgres_endpoint_host,
    create_app,
    get_db,
    orm_db,
    register_user,
    send_conversation_message,
)


class PostgresDatabaseIdentityTests(unittest.TestCase):
    def test_render_runtime_cannot_fall_back_to_sqlite_when_environment_is_misconfigured(self):
        with tempfile.TemporaryDirectory(prefix="openchat-render-config-") as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "RENDER": "true",
                    "APP_ENV": "development",
                    "DATABASE_URL": "",
                    "SECRET_KEY": "stable-diagnostic-test-key",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "DATABASE_URL must be a PostgreSQL connection URL in production",
                ):
                    create_app(
                        {
                            "APP_ENV": "development",
                            "DATABASE": str(Path(temp_dir) / "must-not-be-used.db"),
                            "SECRET_KEY": "stable-diagnostic-test-key",
                        }
                    )

    def test_identity_helper_reports_active_database_without_connection_url(self):
        class FakeConnection:
            def execute(self, query):
                self.query = query
                return self

            def fetchone(self):
                return {
                    "database_name": "openchat_test",
                    "schema_name": "public",
                    "search_path": "public",
                    "tls_enabled": True,
                    "users_table_present": True,
                    "messages_table_present": True,
                }

        identity = _postgres_database_identity(FakeConnection())

        self.assertEqual(identity["database_name"], "openchat_test")
        self.assertEqual(identity["schema_name"], "public")
        self.assertTrue(identity["tls_enabled"])
        self.assertTrue(identity["users_table_present"])
        self.assertTrue(identity["messages_table_present"])

    def test_postgres_endpoint_does_not_expose_credentials(self):
        self.assertEqual(
            _postgres_endpoint_host(
                "postgresql+psycopg://user:secret@ep-example.neon.tech/openchat?sslmode=require"
            ),
            "ep-example.neon.tech",
        )

    def test_database_diagnostics_are_read_only_and_do_not_echo_username(self):
        class FakeConnection:
            def __init__(self):
                self.queries = []
                self.query = ""

            def execute(self, query, parameters=()):
                self.queries.append((query.strip(), parameters))
                self.query = query
                return self

            def fetchone(self):
                if "current_database()" in self.query:
                    return {
                        "database_name": "openchat_test",
                        "schema_name": "public",
                        "search_path": "public",
                        "tls_enabled": True,
                        "users_table_present": True,
                        "messages_table_present": True,
                    }
                return {"count": 3}

            def fetchall(self):
                return [{"id": 42}]

        with tempfile.TemporaryDirectory(prefix="openchat-diagnostic-cli-") as temp_dir:
            application = create_app(
                {
                    "APP_ENV": "development",
                    "TESTING": True,
                    "DATABASE": str(Path(temp_dir) / "diagnostics.db"),
                    "UPLOAD_FOLDER": str(Path(temp_dir) / "uploads"),
                }
            )
            application.config["DATABASE_BACKEND"] = "postgresql"
            application.config["SQLALCHEMY_DATABASE_URI"] = (
                "postgresql+psycopg://user:secret@ep-example.neon.tech/openchat"
            )
            connection = FakeConnection()
            try:
                with patch("app.get_db", return_value=connection):
                    result = application.test_cli_runner().invoke(
                        args=["diagnose-database", "--username", "private-test-account"]
                    )

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("endpoint=ep-example.neon.tech", result.output)
                self.assertIn("account_matches=1", result.output)
                self.assertIn("sent_messages=3", result.output)
                self.assertNotIn("private-test-account", result.output)
                self.assertNotIn("secret", result.output)
                self.assertEqual(
                    [query.split()[0].upper() for query, _ in connection.queries],
                    ["BEGIN", "SELECT", "SELECT", "SELECT", "SELECT"],
                )
                self.assertEqual(connection.queries[-1][1], (42,))
            finally:
                with application.app_context():
                    orm_db.engine.dispose()

    def test_production_session_cookie_security(self):
        class FakeConnection:
            query = ""

            def execute(self, query, parameters=()):
                self.query = query
                return self

            def fetchone(self):
                if "information_schema.tables" in self.query:
                    return (3,)
                return {
                    "database_name": "openchat_test",
                    "schema_name": "public",
                    "search_path": "public",
                    "tls_enabled": True,
                    "users_table_present": True,
                    "messages_table_present": True,
                }

            def fetchall(self):
                return []

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "stable-diagnostic-test-key",
                "DATABASE_URL": "postgresql://test:test@localhost/openchat",
                "OPENCHAT_DEVELOPER_USER_ID": "",
            },
            clear=False,
        ):
            with patch("app.get_db", return_value=FakeConnection()):
                application = create_app(
                    {
                        "APP_ENV": "production",
                        "TESTING": True,
                        "SECRET_KEY": "stable-diagnostic-test-key",
                    }
                )
            try:
                self.assertTrue(application.config["SESSION_COOKIE_SECURE"])
                self.assertTrue(application.config["SESSION_COOKIE_HTTPONLY"])
                self.assertEqual(application.config["SESSION_COOKIE_SAMESITE"], "Lax")
                self.assertIsNone(application.config["SESSION_COOKIE_DOMAIN"])
            finally:
                with application.app_context():
                    orm_db.engine.dispose()


class PersistenceAndSessionTests(unittest.TestCase):
    def test_database_records_survive_app_recreation_independently_of_cookie_key(self):
        with tempfile.TemporaryDirectory(prefix="openchat-persistence-test-") as temp_dir:
            temp_path = Path(temp_dir)
            database_path = temp_path / "application.db"
            base_config = {
                "APP_ENV": "development",
                "TESTING": True,
                "DATABASE": str(database_path),
                "UPLOAD_FOLDER": str(temp_path / "uploads"),
            }
            applications = []
            try:
                with patch.dict(
                    os.environ,
                    {"APP_ENV": "development", "SECRET_KEY": "stable-diagnostic-test-key"},
                    clear=False,
                ):
                    first_app = create_app({**base_config, "SECRET_KEY": "stable-diagnostic-test-key"})
                    applications.append(first_app)
                    with first_app.test_request_context():
                        user = register_user("Persistence Check", "StrongPass123!")
                        send_conversation_message(
                            1,
                            user["id"],
                            "message survives app recreation",
                        )

                    login_response = first_app.test_client().post(
                        "/auth",
                        data={
                            "mode": "login",
                            "username": user["username"],
                            "password": "StrongPass123!",
                        },
                        follow_redirects=False,
                    )
                    self.assertEqual(login_response.status_code, 302)
                    cookie = SimpleCookie()
                    cookie.load(login_response.headers["Set-Cookie"])
                    session_cookie = cookie["session"]
                    self.assertTrue(session_cookie["httponly"])
                    self.assertEqual(session_cookie["samesite"], "Lax")
                    cookie_expiry = parsedate_to_datetime(session_cookie["expires"])
                    self.assertGreater(
                        cookie_expiry,
                        datetime.now(timezone.utc) + timedelta(days=30),
                    )

                    second_app = create_app(
                        {**base_config, "SECRET_KEY": "stable-diagnostic-test-key"}
                    )
                    applications.append(second_app)
                    second_client = second_app.test_client()
                    second_client.set_cookie("session", session_cookie.value)
                    authenticated_response = second_client.get("/auth")
                    self.assertEqual(authenticated_response.status_code, 302)

                    with second_app.app_context():
                        db = get_db()
                        self.assertEqual(
                            db.execute(
                                "SELECT id FROM users WHERE id = ?",
                                (user["id"],),
                            ).fetchone()["id"],
                            user["id"],
                        )
                        self.assertEqual(
                            db.execute(
                                "SELECT COUNT(*) AS count FROM conversation_messages "
                                "WHERE sender_id = ? AND text = ?",
                                (user["id"], "message survives app recreation"),
                            ).fetchone()["count"],
                            1,
                        )

                    third_app = create_app(
                        {**base_config, "SECRET_KEY": "rotated-diagnostic-test-key"}
                    )
                    applications.append(third_app)
                    third_client = third_app.test_client()
                    third_client.set_cookie("session", session_cookie.value)
                    expired_session_response = third_client.get("/auth")
                    self.assertEqual(expired_session_response.status_code, 200)
                    with third_app.app_context():
                        self.assertEqual(
                            get_db().execute(
                                "SELECT COUNT(*) AS count FROM users WHERE id = ?",
                                (user["id"],),
                            ).fetchone()["count"],
                            1,
                        )
            finally:
                for application in applications:
                    with application.app_context():
                        orm_db.engine.dispose()

if __name__ == "__main__":
    unittest.main()
