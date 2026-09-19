import os
import tempfile
import unittest

from app import (
    authenticate_user,
    create_app,
    detect_device_type,
    format_message_time,
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
            self.assertEqual(user["username"], "Alice")
            self.assertTrue(authenticate_user("Alice", "StrongPass123!"))
            self.assertFalse(authenticate_user("Alice", "wrong-password"))

    def test_auth_pages_exist(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/auth").status_code, 200)
        self.assertEqual(client.get("/settings").status_code, 200)

    def test_chat_requires_login(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.get("/messages").status_code, 302)

    def test_signup_requires_unique_username(self):
        with self.app.app_context(), self.app.test_request_context("/"):
            register_user("Alice", "StrongPass123!")
            with self.assertRaises(ValueError):
                register_user("alice", "AnotherPass456!")
            with self.assertRaises(ValueError):
                register_user("Alice", "DifferentPass789!")

    def test_detect_device_type(self):
        self.assertEqual("mobile", detect_device_type("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"))
        self.assertEqual("mobile", detect_device_type("Mozilla/5.0 (Linux; Android 14; Pixel 8)"))
        self.assertEqual("desktop", detect_device_type("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))


if __name__ == "__main__":
    unittest.main()
