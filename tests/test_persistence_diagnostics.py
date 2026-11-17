import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from app import (
    _postgres_database_identity,
    _postgres_endpoint_host,
    _SQLITE_IMPORT_TABLES,
    _register_user_with_password_hash,
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
                if "information_schema.tables" in self.query:
                    return [{"table_name": table_name} for table_name in _SQLITE_IMPORT_TABLES]
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
                self.assertIn("connection_status=connected", result.output)
                self.assertIn("database_engine=postgresql", result.output)
                self.assertIn("required_tables_available=18/18", result.output)
                self.assertIn("rows.users=3", result.output)
                self.assertIn("rows.conversation_read_state=3", result.output)
                self.assertIn("rows.presence=3", result.output)
                self.assertIn("account_matches=1", result.output)
                self.assertIn("sent_messages=3", result.output)
                self.assertNotIn("private-test-account", result.output)
                self.assertNotIn("secret", result.output)
                self.assertEqual(
                    [query.split()[0].upper() for query, _ in connection.queries],
                    ["BEGIN"] + ["SELECT"] * (len(connection.queries) - 1),
                )
                self.assertTrue(all(
                    query.split()[0].upper() in {"BEGIN", "SELECT"}
                    for query, _ in connection.queries
                ))
            finally:
                with application.app_context():
                    orm_db.engine.dispose()

    def test_database_diagnostics_report_connection_failure(self):
        with tempfile.TemporaryDirectory(prefix="openchat-diagnostic-failure-") as temp_dir:
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
                "postgresql+psycopg://user:password@ep-example.neon.tech/openchat"
            )
            try:
                with patch("app.get_db", side_effect=RuntimeError("test connection unavailable")):
                    result = application.test_cli_runner().invoke(args=["diagnose-database"])

                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("connection_status=failed", result.output)
                self.assertNotIn("postgresql+psycopg://", result.output)
                self.assertNotIn("password", result.output)
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

    def test_postgres_connection_failure_raises_without_sqlite_fallback(self):
        class FakeConnection:
            def execute(self, query, parameters=()):
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

        with tempfile.TemporaryDirectory(prefix="openchat-connection-failure-") as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "APP_ENV": "development",
                    "SECRET_KEY": "stable-diagnostic-test-key",
                    "DATABASE_URL": "postgresql://test:test@localhost/openchat",
                },
                clear=False,
            ):
                with patch("app.get_db", return_value=FakeConnection()):
                    application = create_app(
                        {
                            "APP_ENV": "development",
                            "DATABASE_URL": "postgresql://test:test@localhost/openchat",
                            "TESTING": True,
                            "SECRET_KEY": "stable-diagnostic-test-key",
                            "UPLOAD_FOLDER": str(Path(temp_dir) / "uploads"),
                        }
                    )

            try:
                self.assertEqual(application.config["DATABASE_BACKEND"], "postgresql")
                with application.app_context():
                    with patch.object(
                        orm_db.engine,
                        "raw_connection",
                        side_effect=SQLAlchemyError("test connection failure"),
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "configured PostgreSQL database is unavailable",
                        ):
                            get_db()
            finally:
                with application.app_context():
                    orm_db.engine.dispose()


class PersistenceAndSessionTests(unittest.TestCase):
    def test_database_records_survive_a_process_restart(self):
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="openchat-process-restart-") as temp_dir:
            temp_path = Path(temp_dir)
            environment = os.environ.copy()
            environment.pop("RENDER", None)
            environment.update(
                {
                    "APP_ENV": "development",
                    "DATABASE_URL": str(temp_path / "application.db"),
                    "SECRET_KEY": "stable-process-restart-test-key",
                    "OPENCHAT_RESTART_TEST_DATABASE": str(temp_path / "application.db"),
                    "OPENCHAT_RESTART_TEST_UPLOADS": str(temp_path / "uploads"),
                }
            )
            write_process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import os
from app import create_app, register_user, send_conversation_message, orm_db
application = create_app({
    "APP_ENV": "development",
    "TESTING": True,
    "DATABASE": os.environ["OPENCHAT_RESTART_TEST_DATABASE"],
    "UPLOAD_FOLDER": os.environ["OPENCHAT_RESTART_TEST_UPLOADS"],
    "SECRET_KEY": os.environ["SECRET_KEY"],
})
try:
    with application.test_request_context():
        user = register_user("Process Restart", "StrongPass123!")
        send_conversation_message(1, user["id"], "persisted across process restart")
finally:
    with application.app_context():
        orm_db.engine.dispose()
""",
                ],
                cwd=str(repository_root),
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(write_process.returncode, 0, write_process.stderr)

            read_process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import os
from app import create_app, get_db, orm_db
application = create_app({
    "APP_ENV": "development",
    "TESTING": True,
    "DATABASE": os.environ["OPENCHAT_RESTART_TEST_DATABASE"],
    "UPLOAD_FOLDER": os.environ["OPENCHAT_RESTART_TEST_UPLOADS"],
    "SECRET_KEY": os.environ["SECRET_KEY"],
})
try:
    with application.app_context():
        db = get_db()
        user = db.execute(
            "SELECT id FROM users WHERE username = ?",
            ("1, Process Restart",),
        ).fetchone()
        assert user is not None
        message = db.execute(
            "SELECT COUNT(*) AS count FROM conversation_messages "
            "WHERE sender_id = ? AND text = ?",
            (user["id"], "persisted across process restart"),
        ).fetchone()
        assert message["count"] == 1
finally:
    with application.app_context():
        orm_db.engine.dispose()
""",
                ],
                cwd=str(repository_root),
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(read_process.returncode, 0, read_process.stderr)

    def test_registration_builds_username_from_inserted_id(self):
        class FakeCursor:
            lastrowid = 42

        class FakeResult:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeDatabase:
            def __init__(self):
                self.statements = []

            def execute(self, query, parameters=()):
                self.statements.append((query, parameters))
                if query.lstrip().startswith("INSERT INTO users"):
                    return FakeCursor()
                if query.lstrip().startswith("SELECT id, username, created_at FROM users"):
                    return FakeResult(
                        {"id": 42, "username": "42, Concurrent User", "created_at": "now"}
                    )
                return FakeCursor()

            def commit(self):
                self.committed = True

            def rollback(self):
                self.rolled_back = True

        with tempfile.TemporaryDirectory(prefix="openchat-registration-id-") as temp_dir:
            application = create_app(
                {
                    "APP_ENV": "development",
                    "TESTING": True,
                    "DATABASE": str(Path(temp_dir) / "application.db"),
                    "UPLOAD_FOLDER": str(Path(temp_dir) / "uploads"),
                    "SECRET_KEY": "stable-diagnostic-test-key",
                }
            )
            database = FakeDatabase()
            try:
                with application.test_request_context():
                    with patch("app.get_db", return_value=database):
                        with patch("app.ensure_enormous_member"):
                            user = _register_user_with_password_hash(
                                "Concurrent User",
                                "precomputed-password-hash",
                            )

                self.assertEqual(user["id"], 42)
                self.assertEqual(user["username"], "42, Concurrent User")
                self.assertEqual(database.statements[0][1][0], "Concurrent User")
                self.assertEqual(
                    database.statements[1][1],
                    ("42, Concurrent User", 42),
                )
                self.assertTrue(database.committed)
                self.assertFalse(any("MAX(id)" in query for query, _ in database.statements))
            finally:
                with application.app_context():
                    orm_db.engine.dispose()

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
                        self.assertEqual(
                            db.execute(
                                "SELECT COUNT(*) AS count FROM conversations WHERE id = 1"
                            ).fetchone()["count"],
                            1,
                        )
                        self.assertEqual(
                            db.execute(
                                "SELECT COUNT(*) AS count FROM conversation_members "
                                "WHERE conversation_id = 1 AND user_id = ?",
                                (user["id"],),
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

    def test_database_lookup_failure_does_not_clear_session_or_return_empty_history(self):
        with tempfile.TemporaryDirectory(prefix="openchat-session-error-") as temp_dir:
            temp_path = Path(temp_dir)
            application = create_app(
                {
                    "APP_ENV": "development",
                    "TESTING": True,
                    "PROPAGATE_EXCEPTIONS": False,
                    "DATABASE": str(temp_path / "application.db"),
                    "SECRET_KEY": "stable-diagnostic-test-key",
                    "UPLOAD_FOLDER": str(temp_path / "uploads"),
                }
            )
            try:
                client = application.test_client()
                signup_response = client.post(
                    "/auth",
                    data={
                        "mode": "signup",
                        "username": "Session Error Check",
                        "password": "StrongPass123!",
                        "confirm_password": "StrongPass123!",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(signup_response.status_code, 302)

                with patch("app.get_db", side_effect=RuntimeError("test database unavailable")):
                    history_response = client.get("/api/conversations/1/messages")
                self.assertEqual(history_response.status_code, 500)
                self.assertNotEqual(history_response.get_data(as_text=True), "[]")

                still_signed_in = client.get("/auth")
                self.assertEqual(still_signed_in.status_code, 302)
            finally:
                with application.app_context():
                    orm_db.engine.dispose()

if __name__ == "__main__":
    unittest.main()
