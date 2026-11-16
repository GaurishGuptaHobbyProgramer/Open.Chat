import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects import postgresql
from werkzeug.security import check_password_hash, generate_password_hash

from scripts.migrate_sqlite_to_postgres import (
    _synchronize_sequences,
    _target_url_from_environment,
    migrate,
)


def _create_related_databases(source_path, target_path, record_count=150):
    schema = """
        CREATE TABLE parents (
            id INTEGER PRIMARY KEY,
            label TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE children (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL REFERENCES parents(id),
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """
    source = sqlite3.connect(source_path)
    source.executescript(schema)
    source.executemany(
        "INSERT INTO parents VALUES (?, ?, ?, ?)",
        [
            (record_id, f"Local {record_id}", f"local-{record_id}", f"2026-01-{record_id:03}")
            for record_id in range(1, record_count + 1)
        ],
    )
    source.executemany(
        "INSERT INTO children VALUES (?, ?, ?, ?)",
        [
            (record_id, record_id, f"Message {record_id}", f"2026-02-{record_id:03}")
            for record_id in range(1, record_count + 1)
        ],
    )
    source.commit()
    source.close()

    target = sqlite3.connect(target_path)
    target.executescript(schema)
    target.execute(
        "INSERT INTO parents VALUES (1, 'Local 1', 'local-1', '2026-01-001')"
    )
    target.commit()
    target.close()


class GeneratedRecordMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory = tempfile.TemporaryDirectory()
        cls.source_path = Path(cls.temp_directory.name) / "source.db"
        cls.target_path = Path(cls.temp_directory.name) / "target.db"
        cls.backup_directory = Path(cls.temp_directory.name) / "backups"
        _create_related_databases(cls.source_path, cls.target_path)
        cls.engine = create_engine("sqlite:///" + str(cls.target_path))
        cls.first_report = migrate(
            cls.source_path, cls.engine, backup_dir=cls.backup_directory
        )
        cls.rerun_report = migrate(
            cls.source_path, cls.engine, backup_dir=cls.backup_directory
        )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_directory.cleanup()


def _record_migration_test(record_id):
    def test_record_and_foreign_key_are_preserved(self):
        with self.engine.connect() as connection:
            parent = connection.execute(
                text("SELECT label, slug, created_at FROM parents WHERE id = :id"),
                {"id": record_id},
            ).one()
            child = connection.execute(
                text("SELECT parent_id, message, created_at FROM children WHERE id = :id"),
                {"id": record_id},
            ).one()
        if record_id == 1:
            self.assertEqual(parent, ("Local 1", "local-1", "2026-01-001"))
        else:
            self.assertEqual(
                parent,
                (f"Local {record_id}", f"local-{record_id}", f"2026-01-{record_id:03}"),
            )
        self.assertEqual(
            child,
            (record_id, f"Message {record_id}", f"2026-02-{record_id:03}"),
        )

    return test_record_and_foreign_key_are_preserved


for _record_id in range(1, 151):
    setattr(
        GeneratedRecordMigrationTests,
        f"test_record_{_record_id:03d}_preserves_values_and_relationship",
        _record_migration_test(_record_id),
    )


class SqliteToPostgresMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.source_path = Path(self.temp_directory.name) / "source.db"
        self.target_path = Path(self.temp_directory.name) / "target.db"
        self.backup_directory = Path(self.temp_directory.name) / "backups"
        self.engine = None

    def tearDown(self):
        if self.engine is not None:
            self.engine.dispose()
        self.temp_directory.cleanup()

    def create_item_databases(self, source_sql, target_sql):
        source = sqlite3.connect(self.source_path)
        source.executescript(source_sql)
        source.commit()
        source.close()
        target = sqlite3.connect(self.target_path)
        target.executescript(target_sql)
        target.commit()
        target.close()
        self.engine = create_engine("sqlite:///" + str(self.target_path))

    def migrate(self, **kwargs):
        return migrate(
            self.source_path,
            self.engine,
            backup_dir=self.backup_directory,
            **kwargs,
        )

    def test_dry_run_reports_prospective_changes_without_persisting(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'source');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);",
        )

        report = self.migrate(dry_run=True)

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["records_examined"], 1)
        self.assertEqual(report["records_inserted"], 0)
        self.assertEqual(report["would_insert"], 1)
        self.assertIsNone(report["source_backup"])
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM items")), 0)

    def test_primary_key_duplicates_are_skipped_without_overwriting(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'local');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'local');",
        )

        report = self.migrate()

        self.assertEqual(report["records_inserted"], 0)
        self.assertEqual(report["records_skipped"], 1)
        self.assertEqual(report["records_duplicate"], 1)
        self.assertEqual(report["records_conflict"], 0)
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT value FROM items WHERE id = 1")), "local")

    def test_existing_primary_key_with_different_values_aborts_without_changes(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'source');"
            "INSERT INTO items VALUES (2, 'new source row');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'production');",
        )

        report = self.migrate()

        self.assertEqual(report["records_conflict"], 1)
        self.assertEqual(report["records_inserted"], 0)
        self.assertIn("aborted without writing data", report["errors"][0])
        self.assertEqual(
            report["tables"]["items"]["conflict_examples"][0]["source_primary_key"],
            [1],
        )
        with self.engine.connect() as target:
            self.assertEqual(
                target.execute(text("SELECT id, value FROM items ORDER BY id")).all(),
                [(1, "production")],
            )

    def test_unique_conflicts_with_different_ids_are_reported_and_aborted(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE);"
            "INSERT INTO items VALUES (1, 'duplicate-slug');"
            "INSERT INTO items VALUES (2, 'new-slug');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE);"
            "INSERT INTO items VALUES (3, 'duplicate-slug');",
        )

        report = self.migrate()

        self.assertEqual(report["records_conflict"], 1)
        self.assertEqual(report["records_inserted"], 0)
        self.assertIn("aborted without writing data", report["errors"][0])
        with self.engine.connect() as target:
            self.assertEqual(
                target.execute(text("SELECT id, slug FROM items")).one(),
                (3, "duplicate-slug"),
            )

    def test_source_rows_conflicting_with_destination_unique_key_abort(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, slug TEXT NOT NULL, email TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'same-slug', 'same@example.test');"
            "INSERT INTO items VALUES (2, 'same-slug', 'same@example.test');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, "
            "email TEXT NOT NULL UNIQUE);",
        )

        report = self.migrate()

        self.assertEqual(report["records_conflict"], 2)
        self.assertEqual(report["tables"]["items"]["source_unique_conflicts"], 2)
        self.assertEqual(
            report["tables"]["items"]["source_duplicate_keys"],
            {"email": 1, "slug": 1},
        )
        self.assertEqual(report["records_inserted"], 0)
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM items")), 0)

    def test_source_foreign_key_violation_prevents_any_migration(self):
        self.create_item_databases(
            "CREATE TABLE parents (id INTEGER PRIMARY KEY);"
            "CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER "
            "REFERENCES parents(id));"
            "INSERT INTO children VALUES (1, 999);",
            "CREATE TABLE parents (id INTEGER PRIMARY KEY);"
            "CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER "
            "REFERENCES parents(id));",
        )

        report = self.migrate()

        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("foreign-key violations", report["errors"][0])
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM parents")), 0)
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM children")), 0)

    def test_schema_mismatch_is_reported_before_inserting(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'source');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, different TEXT NOT NULL);",
        )

        report = self.migrate()

        self.assertEqual(report["records_examined"], 0)
        self.assertIn("schema validation failed", report["errors"][0])

    def test_empty_database_migration_creates_verified_backup(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);",
        )

        report = self.migrate()

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["records_examined"], 0)
        self.assertTrue(Path(report["source_backup"]).is_file())
        backup = sqlite3.connect(report["source_backup"])
        try:
            self.assertEqual(backup.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
        finally:
            backup.close()

    def test_backup_and_migration_leave_the_source_database_unchanged(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (7, 'unchanged');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);",
        )
        original_digest = hashlib.sha256(self.source_path.read_bytes()).digest()

        report = self.migrate()

        self.assertEqual(report["errors"], [])
        self.assertEqual(hashlib.sha256(self.source_path.read_bytes()).digest(), original_digest)
        self.assertEqual(report["records_inserted"], 1)

    def test_destination_only_tables_and_defaulted_columns_are_preserved(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'source');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL, "
            "deployed_flag INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE developer_accounts (user_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL);"
            "INSERT INTO developer_accounts VALUES (9, 'deployed');",
        )

        report = self.migrate(dry_run=True)

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["would_insert"], 1)
        self.assertTrue(report["tables"]["developer_accounts"]["destination_only"])
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM items")), 0)
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM developer_accounts")), 1)

    def test_user_ids_password_hashes_and_developer_grants_are_preserved(self):
        password_hash = generate_password_hash("MigrationSafePass123!")
        schema = """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE developer_accounts (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                created_at TEXT NOT NULL
            );
        """
        source = sqlite3.connect(self.source_path)
        source.executescript(schema)
        source.execute(
            "INSERT INTO users VALUES (41, 'Developer', ?, '2026-01-01T00:00:00Z')",
            (password_hash,),
        )
        source.execute(
            "INSERT INTO developer_accounts VALUES (41, '2026-01-01T00:00:00Z')"
        )
        source.commit()
        source.close()
        target = sqlite3.connect(self.target_path)
        target.executescript(schema)
        target.close()
        self.engine = create_engine("sqlite:///" + str(self.target_path))

        report = self.migrate()

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["records_inserted"], 2)
        with self.engine.connect() as connection:
            user = connection.execute(
                text("SELECT id, username, password_hash FROM users")
            ).one()
            grant = connection.execute(
                text("SELECT user_id FROM developer_accounts")
            ).one()
        self.assertEqual(user.id, 41)
        self.assertEqual(user.username, "Developer")
        self.assertEqual(user.password_hash, password_hash)
        self.assertTrue(check_password_hash(user.password_hash, "MigrationSafePass123!"))
        self.assertEqual(grant.user_id, 41)

    def test_boolean_datetime_and_json_values_are_converted_and_verified(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, enabled INTEGER, "
            "created_at TEXT, payload TEXT);"
            "INSERT INTO items VALUES (1, 1, '2026-04-05T06:07:08', '{\"ok\": true}');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, enabled BOOLEAN, "
            "created_at DATETIME, payload JSON);",
        )

        report = self.migrate()

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["records_inserted"], 1)
        with self.engine.connect() as target:
            row = target.execute(
                text("SELECT enabled, created_at, payload FROM items WHERE id = 1")
            ).one()
        self.assertTrue(row.enabled)
        self.assertEqual(
            datetime.fromisoformat(row.created_at).isoformat(),
            "2026-04-05T06:07:08",
        )
        self.assertEqual(json.loads(row.payload), {"ok": True})

    def test_verify_mode_requires_every_source_row_to_match(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'source');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);",
        )

        missing = self.migrate(verify_only=True)
        self.assertIn("1 source rows are missing", missing["errors"][0])
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM items")), 0)

        imported = self.migrate()
        self.assertEqual(imported["errors"], [])
        verified = self.migrate(verify_only=True)
        self.assertEqual(verified["errors"], [])
        self.assertEqual(verified["records_duplicate"], 1)
        self.assertIsNone(verified["source_backup"])

    def test_sequence_sync_uses_transactional_restart_without_setval(self):
        class FakeInspector:
            def get_columns(self, table_name):
                return [{"name": "id"}]

        class FakeResult:
            def __init__(self, value=None, row=None):
                self.value = value
                self.row = row

            def scalar_one_or_none(self):
                return self.value

            def one_or_none(self):
                return self.row

            def one(self):
                return self.row

            def scalar_one(self):
                return self.value

        class FakeConnection:
            dialect = postgresql.dialect()

            def __init__(self):
                self.statements = []

            def execute(self, statement, parameters=None):
                sql = str(statement)
                self.statements.append(sql)
                if "pg_get_serial_sequence" in sql:
                    return FakeResult(value="public.items_id_seq")
                if "FROM pg_class AS sequence" in sql:
                    return FakeResult(row=("public", "items_id_seq", 1))
                if "SELECT last_value, is_called" in sql:
                    return FakeResult(row=SimpleNamespace(last_value=200, is_called=True))
                if "SELECT MAX" in sql:
                    return FakeResult(value=50)
                return FakeResult()

        connection = FakeConnection()
        with unittest.mock.patch(
            "scripts.migrate_sqlite_to_postgres.inspect",
            return_value=FakeInspector(),
        ):
            _synchronize_sequences(connection, ["items"], "public")

        sequence_statements = [
            statement
            for statement in connection.statements
            if statement.upper().startswith("ALTER SEQUENCE")
        ]
        self.assertEqual(len(sequence_statements), 1)
        self.assertIn("ALTER SEQUENCE", sequence_statements[0])
        self.assertIn("RESTART WITH 201", sequence_statements[0])
        self.assertFalse(any("setval" in statement.lower() for statement in connection.statements))

    def test_interrupted_transaction_rolls_back_and_can_be_retried(self):
        _create_related_databases(self.source_path, self.target_path, record_count=2)
        target = sqlite3.connect(self.target_path)
        target.execute("DELETE FROM parents")
        target.commit()
        target.close()
        self.engine = create_engine("sqlite:///" + str(self.target_path))
        interrupted = {"value": False}

        def interrupt_before_child_insert(connection, cursor, statement, parameters, context, executemany):
            normalized = statement.lstrip().upper()
            if not interrupted["value"] and "INSERT INTO MAIN.CHILDREN" in normalized:
                interrupted["value"] = True
                raise RuntimeError("simulated interruption")

        event.listen(self.engine, "before_cursor_execute", interrupt_before_child_insert)
        failed_report = self.migrate()
        event.remove(self.engine, "before_cursor_execute", interrupt_before_child_insert)

        self.assertEqual(len(failed_report["errors"]), 1)
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM parents")), 0)
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM children")), 0)

        retry_report = self.migrate()
        self.assertEqual(retry_report["errors"], [])
        self.assertEqual(retry_report["records_inserted"], 4)
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM parents")), 2)
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM children")), 2)

    def test_target_url_requires_a_postgresql_database_url(self):
        for value in ("", "sqlite:///local.db"):
            with self.subTest(value=value), unittest.mock.patch.dict(
                os.environ, {"DATABASE_URL": value}, clear=False
            ):
                with self.assertRaises(ValueError):
                    _target_url_from_environment()

    def test_target_url_normalizes_render_postgres_scheme_without_exposing_credentials(self):
        with unittest.mock.patch.dict(
            os.environ,
            {"DATABASE_URL": "postgres://migration-user:secret@db.example/openchat"},
            clear=False,
        ):
            url = _target_url_from_environment()

        self.assertEqual(url.drivername, "postgresql+psycopg")
        self.assertEqual(url.query["sslmode"], "require")
        self.assertEqual(url.password, "secret")
        self.assertNotIn("secret", url.render_as_string(hide_password=True))

    def test_target_url_rejects_postgresql_without_tls(self):
        with unittest.mock.patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://migration-user:secret@db.example/openchat?sslmode=disable"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "must use TLS"):
                _target_url_from_environment()


if __name__ == "__main__":
    unittest.main()
