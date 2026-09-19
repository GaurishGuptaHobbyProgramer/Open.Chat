import os
import tempfile
import unittest

from app import create_app, detect_device_type, format_message_time, get_messages, save_message


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
        self.assertEqual("02/01/2025, 08:05 AM", format_message_time("2025-01-02 08:05:00"))

    def test_detect_device_type(self):
        self.assertEqual("mobile", detect_device_type("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"))
        self.assertEqual("mobile", detect_device_type("Mozilla/5.0 (Linux; Android 14; Pixel 8)"))
        self.assertEqual("desktop", detect_device_type("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))


if __name__ == "__main__":
    unittest.main()
