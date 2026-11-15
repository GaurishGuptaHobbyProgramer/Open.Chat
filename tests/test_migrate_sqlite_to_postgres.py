import os
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from sqlalchemy import create_engine, event, text

from scripts.migrate_sqlite_to_postgres import _target_url_from_environment, migrate


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
        "INSERT INTO parents VALUES (1, 'Production account', 'production-1', 'production-time')"
    )
    target.commit()
    target.close()


class GeneratedRecordMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory = tempfile.TemporaryDirectory()
        cls.source_path = Path(cls.temp_directory.name) / "source.db"
        cls.target_path = Path(cls.temp_directory.name) / "target.db"
        _create_related_databases(cls.source_path, cls.target_path)
        cls.engine = create_engine("sqlite:///" + str(cls.target_path))
        cls.first_report = migrate(cls.source_path, cls.engine)
        cls.rerun_report = migrate(cls.source_path, cls.engine)

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
            self.assertEqual(parent, ("Production account", "production-1", "production-time"))
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

    def test_dry_run_reports_prospective_changes_without_persisting(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'source');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);",
        )

        report = migrate(self.source_path, self.engine, dry_run=True)

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["records_examined"], 1)
        self.assertEqual(report["records_inserted"], 0)
        self.assertEqual(report["would_insert"], 1)
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM items")), 0)

    def test_primary_key_duplicates_are_skipped_without_overwriting(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'local');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'production');",
        )

        report = migrate(self.source_path, self.engine)

        self.assertEqual(report["records_inserted"], 0)
        self.assertEqual(report["records_skipped"], 1)
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT value FROM items WHERE id = 1")), "production")

    def test_unique_conflicts_with_different_ids_are_skipped(self):
        self.create_item_databases(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE);"
            "INSERT INTO items VALUES (1, 'duplicate-slug');",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE);"
            "INSERT INTO items VALUES (2, 'duplicate-slug');",
        )

        report = migrate(self.source_path, self.engine)

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["records_inserted"], 0)
        self.assertEqual(report["records_skipped"], 1)
        with self.engine.connect() as target:
            self.assertEqual(
                target.execute(text("SELECT id, slug FROM items")).one(),
                (2, "duplicate-slug"),
            )

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

        report = migrate(self.source_path, self.engine)

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

        report = migrate(self.source_path, self.engine)

        self.assertEqual(report["records_examined"], 0)
        self.assertIn("schema validation failed", report["errors"][0])

    def test_interrupted_transaction_rolls_back_and_can_be_retried(self):
        _create_related_databases(self.source_path, self.target_path, record_count=2)
        self.engine = create_engine("sqlite:///" + str(self.target_path))
        interrupted = {"value": False}

        def interrupt_before_child_insert(connection, cursor, statement, parameters, context, executemany):
            if not interrupted["value"] and statement.lstrip().upper().startswith("INSERT INTO CHILDREN"):
                interrupted["value"] = True
                raise RuntimeError("simulated interruption")

        event.listen(self.engine, "before_cursor_execute", interrupt_before_child_insert)
        failed_report = migrate(self.source_path, self.engine)
        event.remove(self.engine, "before_cursor_execute", interrupt_before_child_insert)

        self.assertEqual(len(failed_report["errors"]), 1)
        with self.engine.connect() as target:
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM parents")), 1)
            self.assertEqual(target.scalar(text("SELECT COUNT(*) FROM children")), 0)

        retry_report = migrate(self.source_path, self.engine)
        self.assertEqual(retry_report["errors"], [])
        self.assertEqual(retry_report["records_inserted"], 3)
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
