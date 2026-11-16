import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fix_duplicate_users import find_duplicate_user_groups


class DuplicateUserReportTests(unittest.TestCase):
    def test_duplicate_report_preserves_user_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "users.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT)"
                )
                connection.executemany(
                    "INSERT INTO users VALUES (?, ?, ?)",
                    [
                        (10, " Example ", "hash-a"),
                        (25, "example", "hash-b"),
                        (30, "other", "hash-c"),
                    ],
                )
                connection.commit()

            duplicates = find_duplicate_user_groups(database_path)

            self.assertEqual(len(duplicates), 1)
            self.assertEqual(duplicates[0][1], [10, 25])
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT id, password_hash FROM users ORDER BY id").fetchall(),
                    [(10, "hash-a"), (25, "hash-b"), (30, "hash-c")],
                )


if __name__ == "__main__":
    unittest.main()
