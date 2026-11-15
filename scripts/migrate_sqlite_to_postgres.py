#!/usr/bin/env python3
"""Manually copy new SQLite rows into PostgreSQL without replacing existing rows."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.type_api import TypeEngine


_INTERNAL_TABLES = {"alembic_version"}
_BATCH_SIZE = 500


def _sqlite_uri(path: Path) -> str:
    return "file:" + quote(str(path.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        if row[0] not in _INTERNAL_TABLES
    }


def _sqlite_table_schema(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    quoted = '"' + table.replace('"', '""') + '"'
    columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
    primary_key = tuple(
        row[1]
        for row in sorted((row for row in columns if row[5]), key=lambda row: row[5])
    )
    column_specs = {
        row[1]: (
            _sqlite_type_family(row[2]),
            bool(row[3] == 0 and row[5] == 0),
        )
        for row in columns
    }

    unique_keys = set()
    for index in connection.execute(f"PRAGMA index_list({quoted})"):
        if not index[2] or (len(index) > 3 and index[3] == "pk"):
            continue
        index_name = '"' + index[1].replace('"', '""') + '"'
        key_columns = tuple(
            row[2]
            for row in connection.execute(f"PRAGMA index_info({index_name})")
        )
        if key_columns and all(key_columns):
            unique_keys.add(frozenset(key_columns))

    foreign_key_groups: dict[int, list[tuple[int, str, str, str]]] = defaultdict(list)
    for row in connection.execute(f"PRAGMA foreign_key_list({quoted})"):
        foreign_key_groups[row[0]].append((row[1], row[2], row[3], row[4]))
    foreign_keys = set()
    for group in foreign_key_groups.values():
        group.sort(key=lambda item: item[0])
        foreign_keys.add(
            (
                tuple(item[2] for item in group),
                group[0][1],
                tuple(item[3] for item in group),
            )
        )
    return {
        "columns": column_specs,
        "primary_key": primary_key,
        "unique_keys": unique_keys,
        "foreign_keys": foreign_keys,
    }


def _sqlite_type_family(declared_type: str) -> str:
    kind = declared_type.upper()
    if "INT" in kind:
        return "integer"
    if any(part in kind for part in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if any(part in kind for part in ("REAL", "FLOA", "DOUB", "NUM")):
        return "numeric"
    if "BLOB" in kind or not kind:
        return "binary"
    return "numeric"


def _target_type_family(sql_type: TypeEngine[Any]) -> str:
    name = type(sql_type).__name__.lower()
    if "bool" in name:
        return "boolean"
    if any(part in name for part in ("integer", "bigint", "smallint")):
        return "integer"
    if any(part in name for part in ("char", "text", "string", "enum")):
        return "text"
    if any(part in name for part in ("float", "numeric", "decimal", "real")):
        return "numeric"
    if any(part in name for part in ("binary", "blob")):
        return "binary"
    if any(part in name for part in ("date", "time")):
        return "datetime"
    return name


def _target_table_schema(inspector: Any, table: str) -> dict[str, Any]:
    columns = inspector.get_columns(table)
    primary_key = tuple(inspector.get_pk_constraint(table).get("constrained_columns") or ())
    primary_key_set = set(primary_key)
    column_specs = {
        column["name"]: (
            _target_type_family(column["type"]),
            bool(column["nullable"] and column["name"] not in primary_key_set),
        )
        for column in columns
    }

    unique_keys = {
        frozenset(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table)
        if constraint.get("column_names")
    }
    for index in inspector.get_indexes(table):
        if index.get("unique") and index.get("column_names"):
            unique_keys.add(frozenset(index["column_names"]))

    foreign_keys = {
        (
            tuple(constraint["constrained_columns"]),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
        )
        for constraint in inspector.get_foreign_keys(table)
    }
    return {
        "columns": column_specs,
        "primary_key": primary_key,
        "unique_keys": unique_keys,
        "foreign_keys": foreign_keys,
    }


def _validate_schemas(source: sqlite3.Connection, target: Any) -> tuple[list[str], dict[str, dict[str, Any]]]:
    source_tables = _table_names(source)
    target_inspector = inspect(target)
    target_tables = set(target_inspector.get_table_names()).difference(_INTERNAL_TABLES)
    differences = []

    missing_target = sorted(source_tables - target_tables)
    missing_source = sorted(target_tables - source_tables)
    if missing_target:
        differences.append("tables missing from PostgreSQL: " + ", ".join(missing_target))
    if missing_source:
        differences.append("tables missing from SQLite: " + ", ".join(missing_source))
    if not source_tables:
        differences.append("SQLite contains no application tables")

    schemas = {}
    for table in sorted(source_tables & target_tables):
        source_schema = _sqlite_table_schema(source, table)
        target_schema = _target_table_schema(target_inspector, table)
        schemas[table] = source_schema
        if source_schema["columns"] != target_schema["columns"]:
            source_columns = source_schema["columns"]
            target_columns = target_schema["columns"]
            details = []
            if source_columns.keys() != target_columns.keys():
                details.append(
                    "columns differ (SQLite-only: "
                    + ", ".join(sorted(source_columns.keys() - target_columns.keys()))
                    + "; PostgreSQL-only: "
                    + ", ".join(sorted(target_columns.keys() - source_columns.keys()))
                    + ")"
                )
            else:
                details.append("column types or nullability differ")
            differences.append(f"table {table!r}: " + "; ".join(details))
        for key in ("primary_key", "unique_keys", "foreign_keys"):
            if source_schema[key] != target_schema[key]:
                differences.append(f"table {table!r}: {key.replace('_', ' ')} differ")

    return differences, schemas


def _dependency_order(schemas: dict[str, dict[str, Any]]) -> list[str]:
    dependencies = {table: set() for table in schemas}
    for table, schema in schemas.items():
        for _, referenced_table, _ in schema["foreign_keys"]:
            if referenced_table not in schemas:
                raise ValueError(
                    f"Table {table!r} references missing source table {referenced_table!r}."
                )
            if referenced_table != table:
                dependencies[table].add(referenced_table)

    order = []
    remaining = set(dependencies)
    while remaining:
        ready = sorted(table for table in remaining if not dependencies[table] & remaining)
        if not ready:
            raise ValueError(
                "The source schema has cyclic foreign-key dependencies; "
                "the migration cannot safely order these tables."
            )
        order.extend(ready)
        remaining.difference_update(ready)
    return order


def _source_error(source: sqlite3.Connection) -> str | None:
    integrity = source.execute("PRAGMA quick_check").fetchone()[0]
    if integrity != "ok":
        return f"SQLite integrity check failed: {integrity}"
    violations = source.execute("PRAGMA foreign_key_check").fetchmany(5)
    if violations:
        examples = ", ".join(
            f"{row[0]} row {row[1]} references {row[2]}" for row in violations
        )
        return "SQLite has foreign-key violations: " + examples
    return None


def _format_error(error: BaseException) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "Migration interrupted; the PostgreSQL transaction was rolled back."
    if isinstance(error, (ValueError, FileNotFoundError, OSError)):
        return str(error)
    original = getattr(error, "orig", error)
    primary = getattr(getattr(original, "diag", None), "message_primary", None)
    if primary:
        return primary
    if isinstance(error, SQLAlchemyError):
        return (
            f"{type(error).__name__}: database details omitted to protect "
            "credentials and row data"
        )
    return f"{type(error).__name__}: {error}"


def _synchronize_sequences(connection: Any, tables: list[str], schema_name: str | None) -> None:
    preparer = connection.dialect.identifier_preparer
    for table in tables:
        if "id" not in {column["name"] for column in inspect(connection).get_columns(table)}:
            continue
        qualified_table = preparer.quote(table)
        if schema_name:
            qualified_table = preparer.quote_schema(schema_name) + "." + qualified_table
        sequence = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": qualified_table},
        ).scalar_one()
        if sequence:
            connection.execute(
                text(
                    f"SELECT setval(:sequence, "
                    f"GREATEST(COALESCE((SELECT MAX(id) FROM {qualified_table}), 1), "
                    "nextval(:sequence)), true)"
                ),
                {"sequence": sequence},
            )


def migrate(
    source_path: str | os.PathLike[str],
    target_engine: Engine,
    *,
    dry_run: bool = False,
    batch_size: int = _BATCH_SIZE,
) -> dict[str, Any]:
    """Validate both schemas and copy source rows in one rollback-safe transaction."""
    started = time.monotonic()
    report: dict[str, Any] = {
        "tables": {},
        "errors": [],
        "records_examined": 0,
        "records_inserted": 0,
        "records_skipped": 0,
        "would_insert": 0,
        "duration_seconds": 0.0,
    }
    source = None
    transaction = None
    try:
        if batch_size < 1:
            raise ValueError("Batch size must be a positive integer.")
        path = Path(source_path)
        if not path.is_file():
            raise FileNotFoundError(f"SQLite source database does not exist: {path}")
        source = sqlite3.connect(_sqlite_uri(path), uri=True)
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        source_error = _source_error(source)
        if source_error:
            raise ValueError(source_error)

        with target_engine.connect() as target:
            transaction = target.begin()
            differences, schemas = _validate_schemas(source, target)
            if differences:
                raise ValueError("Database schema validation failed: " + "; ".join(differences))
            order = _dependency_order(schemas)
            metadata = MetaData()
            target_tables = {
                name: Table(name, metadata, autoload_with=target)
                for name in order
            }
            target_schema = inspect(target).default_schema_name

            if target.dialect.name == "postgresql":
                insert_function = postgresql_insert
            elif target.dialect.name == "sqlite":
                insert_function = sqlite_insert
            else:
                raise ValueError("The destination must be PostgreSQL.")

            for table_name in order:
                table = target_tables[table_name]
                primary_key = list(schemas[table_name]["primary_key"])
                if not primary_key:
                    raise ValueError(f"Table {table_name!r} has no primary key.")
                columns = list(schemas[table_name]["columns"])
                source_cursor = source.execute(
                    'SELECT * FROM "' + table_name.replace('"', '""') + '"'
                )
                table_report = report["tables"].setdefault(
                    table_name,
                    {"examined": 0, "inserted": 0, "skipped": 0, "would_insert": 0},
                )
                while True:
                    rows = source_cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    batch = [
                        {column: row[column] for column in columns}
                        for row in rows
                    ]
                    table_report["examined"] += len(batch)
                    report["records_examined"] += len(batch)
                    statement = (
                        insert_function(table)
                        .values(batch)
                        .on_conflict_do_nothing()
                        .returning(*(table.c[name] for name in primary_key))
                    )
                    try:
                        inserted_count = len(target.execute(statement).fetchall())
                    except SQLAlchemyError as error:
                        first_key = tuple(batch[0][name] for name in primary_key)
                        last_key = tuple(batch[-1][name] for name in primary_key)
                        raise RuntimeError(
                            f"Insert failed for {table_name} source batch of {len(batch)} "
                            f"rows (primary keys {first_key} through {last_key}): "
                            f"{_format_error(error)}"
                        ) from error
                    table_report["would_insert"] += inserted_count
                    table_report["skipped"] += len(batch) - inserted_count

            if not dry_run and target.dialect.name == "postgresql":
                inserted_tables = [
                    table for table, counts in report["tables"].items()
                    if counts["would_insert"]
                ]
                _synchronize_sequences(target, inserted_tables, target_schema)

            if dry_run:
                transaction.rollback()
            else:
                transaction.commit()
    except (Exception, KeyboardInterrupt) as error:
        if transaction is not None and transaction.is_active:
            transaction.rollback()
        report["errors"].append(_format_error(error))
        report["records_inserted"] = 0
        report["records_skipped"] = sum(
            counts["skipped"] for counts in report["tables"].values()
        )
        for counts in report["tables"].values():
            counts["inserted"] = 0
            counts["would_insert"] = 0
    else:
        for counts in report["tables"].values():
            counts["inserted"] = 0 if dry_run else counts["would_insert"]
            report["records_inserted"] += counts["inserted"]
            report["records_skipped"] += counts["skipped"]
            report["would_insert"] += counts["would_insert"]
    finally:
        if source is not None:
            source.close()
        report["duration_seconds"] = time.monotonic() - started
    return report


def _target_url_from_environment() -> URL:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise ValueError("Set DATABASE_URL to the destination PostgreSQL connection URL.")
    url = make_url(value)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername not in {"postgresql+psycopg", "postgresql+psycopg2"}:
        raise ValueError("DATABASE_URL must use a PostgreSQL scheme.")
    sslmode = url.query.get("sslmode")
    if isinstance(sslmode, tuple):
        if len(sslmode) > 1:
            raise ValueError("Specify PostgreSQL sslmode only once.")
        sslmode = sslmode[0] if sslmode else None
    if sslmode is None:
        url = url.update_query_dict({"sslmode": "require"})
    elif sslmode.lower() not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("PostgreSQL connections must use TLS (sslmode=require or stronger).")
    return url


def _print_report(report: dict[str, Any], *, dry_run: bool) -> None:
    print("Migration report")
    for table, counts in sorted(report["tables"].items()):
        print(
            f"  {table}: examined={counts['examined']}, "
            f"inserted={counts['inserted']}, skipped={counts['skipped']}"
            + (f", would_insert={counts['would_insert']}" if dry_run else "")
        )
    print(f"Tables processed: {len(report['tables'])}")
    print(f"Records examined: {report['records_examined']}")
    print(f"Records inserted: {report['records_inserted']}")
    print(f"Records skipped: {report['records_skipped']}")
    if dry_run:
        print(f"Records that would be inserted: {report['would_insert']}")
    print(f"Errors: {len(report['errors'])}")
    for error in report["errors"]:
        print(f"  ERROR: {error}", file=sys.stderr)
    print(f"Total migration duration: {report['duration_seconds']:.2f} seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time, repeatable SQLite-to-PostgreSQL data migration."
    )
    parser.add_argument("--source", default="chat.db", help="SQLite file (default: chat.db)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform inserts inside a transaction, report projected changes, then roll back.",
    )
    args = parser.parse_args(argv)

    engine = None
    started = time.monotonic()
    try:
        engine = create_engine(_target_url_from_environment(), pool_pre_ping=True)
        report = migrate(args.source, engine, dry_run=args.dry_run)
    except KeyboardInterrupt:
        report = {
            "tables": {},
            "errors": ["Migration interrupted; the PostgreSQL transaction was rolled back."],
            "records_examined": 0,
            "records_inserted": 0,
            "records_skipped": 0,
            "would_insert": 0,
            "duration_seconds": time.monotonic() - started,
        }
    except Exception as error:
        report = {
            "tables": {},
            "errors": [_format_error(error)],
            "records_examined": 0,
            "records_inserted": 0,
            "records_skipped": 0,
            "would_insert": 0,
            "duration_seconds": time.monotonic() - started,
        }
    finally:
        if engine is not None:
            engine.dispose()

    _print_report(report, dry_run=args.dry_run)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
