import os
import sqlite3
import tempfile
import unittest

from app import (
    authenticate_user,
    create_app,
    detect_device_type,
    format_message_time,
    get_db,
    get_messages,
    register_user,
    save_message,
)


class ChatDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.app = create_app({"TESTING": True, "DATABASE": self.db_path})

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

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

    def test_auth_pages_exist(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/auth").status_code, 200)
        self.assertEqual(client.get("/settings").status_code, 200)

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


if __name__ == "__main__":
    unittest.main()
