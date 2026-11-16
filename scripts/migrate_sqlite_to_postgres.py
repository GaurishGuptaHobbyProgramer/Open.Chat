#!/usr/bin/env python3
"""Manually copy new SQLite rows into PostgreSQL without replacing existing rows."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import MetaData, Table, and_, create_engine, func, inspect, or_, select, text, tuple_
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
    if "BOOL" in kind:
        return "boolean"
    if any(part in kind for part in ("DATE", "TIME")):
        return "datetime"
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
    if "json" in name:
        return "json"
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
            column.get("default") is not None or column.get("identity") is not None,
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
    if missing_target:
        differences.append("tables missing from PostgreSQL: " + ", ".join(missing_target))
    if not source_tables:
        differences.append("SQLite contains no application tables")

    schemas = {}
    for table in sorted(source_tables & target_tables):
        source_schema = _sqlite_table_schema(source, table)
        target_schema = _target_table_schema(target_inspector, table)
        schemas[table] = source_schema
        source_columns = source_schema["columns"]
        target_columns = target_schema["columns"]
        missing_columns = sorted(source_columns.keys() - target_columns.keys())
        if missing_columns:
            differences.append(
                f"table {table!r}: PostgreSQL is missing source columns "
                + ", ".join(missing_columns)
            )
        for column in sorted(source_columns.keys() & target_columns.keys()):
            source_family, source_nullable = source_columns[column]
            target_family, target_nullable, _ = target_columns[column]
            compatible_families = {
                (source_family, target_family),
                ("integer", "numeric"),
                ("integer", "boolean"),
                ("boolean", "integer"),
                ("text", "datetime"),
                ("text", "json"),
            }
            if (source_family, target_family) not in compatible_families:
                differences.append(
                    f"table {table!r}, column {column!r}: incompatible SQLite "
                    f"{source_family} and PostgreSQL {target_family} types"
                )
            if source_nullable and not target_nullable:
                differences.append(
                    f"table {table!r}, column {column!r}: PostgreSQL is NOT NULL "
                    "but SQLite permits NULL"
                )

        for column in sorted(target_columns.keys() - source_columns.keys()):
            _, target_nullable, has_default = target_columns[column]
            if not target_nullable and not has_default:
                differences.append(
                    f"table {table!r}: source is missing required PostgreSQL column "
                    f"{column!r} with no default"
                )

        if source_schema["primary_key"] != target_schema["primary_key"]:
            differences.append(f"table {table!r}: primary key differs")
        if not source_schema["unique_keys"].issubset(target_schema["unique_keys"]):
            differences.append(f"table {table!r}: PostgreSQL is missing source unique constraints")
        for key in target_schema["unique_keys"]:
            missing_key_columns = key - source_columns.keys()
            if missing_key_columns:
                differences.append(
                    f"table {table!r}: PostgreSQL unique constraint uses source-missing "
                    "columns " + ", ".join(sorted(missing_key_columns))
                )
        if not source_schema["foreign_keys"].issubset(target_schema["foreign_keys"]):
            differences.append(f"table {table!r}: PostgreSQL is missing source foreign keys")

    return differences, schemas


def _dependency_order(
    schemas: dict[str, dict[str, Any]],
    target_inspector: Any,
) -> list[str]:
    dependencies = {table: set() for table in schemas}
    for table in schemas:
        for foreign_key in target_inspector.get_foreign_keys(table):
            referenced_table = foreign_key["referred_table"]
            if referenced_table not in schemas:
                continue
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


def _create_sqlite_backup(source_path: Path, backup_directory: str | os.PathLike[str] | None = None) -> Path:
    directory = Path(
        backup_directory
        or os.environ.get("OPENCHAT_SQLITE_BACKUP_DIR")
        or Path.home() / "OpenChatBackups"
    ).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    backup_path = directory / (
        f"openchat-sqlite-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-"
        f"{os.urandom(4).hex()}.db"
    )
    source = sqlite3.connect(_sqlite_uri(source_path), uri=True)
    backup = None
    try:
        backup = sqlite3.connect(str(backup_path))
        source.backup(backup)
        integrity = backup.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite backup integrity check failed: {integrity}")
        source_tables = _table_names(source)
        backup_tables = _table_names(backup)
        if source_tables != backup_tables:
            raise RuntimeError("SQLite backup verification failed: table inventory differs.")
        for table_name in sorted(source_tables):
            quoted = '"' + table_name.replace('"', '""') + '"'
            source_count = source.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            backup_count = backup.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            if source_count != backup_count:
                raise RuntimeError(
                    f"SQLite backup verification failed for table {table_name!r}: row counts differ."
                )
        violations = backup.execute("PRAGMA foreign_key_check").fetchmany(1)
        if violations:
            raise RuntimeError("SQLite backup verification failed: foreign-key violations exist.")
    except Exception:
        if backup is not None:
            backup.close()
        source.close()
        if backup_path.exists():
            backup_path.unlink()
        raise
    finally:
        if backup is not None:
            backup.close()
        source.close()
    return backup_path


def _convert_value(value: Any, target_type: TypeEngine[Any]) -> Any:
    if value is None:
        return None
    family = _target_type_family(target_type)
    if family == "boolean":
        if value in (0, 1, False, True):
            return bool(value)
        raise ValueError("A source value cannot be converted to the PostgreSQL boolean column.")
    if family == "datetime" and isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        type_name = type(target_type).__name__.lower()
        try:
            if type_name == "date":
                return date.fromisoformat(normalized[:10])
            if type_name == "time":
                return datetime_time.fromisoformat(normalized)
            return datetime.fromisoformat(normalized)
        except ValueError as error:
            raise ValueError("A source value cannot be converted to the PostgreSQL date/time column.") from error
    if family == "json" and isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError("A source value is not valid JSON for the PostgreSQL JSON column.") from error
    return value


def _key_value(row: dict[str, Any], columns: tuple[str, ...]) -> tuple[Any, ...] | None:
    value = tuple(row[column] for column in columns)
    return None if any(part is None for part in value) else value


def _source_duplicate_key_counts(
    source: sqlite3.Connection,
    table_name: str,
    unique_keys: list[tuple[str, ...]],
) -> dict[str, int]:
    quoted_table = '"' + table_name.replace('"', '""') + '"'
    duplicates = {}
    for key in unique_keys:
        quoted_columns = ['"' + column.replace('"', '""') + '"' for column in key]
        columns = ", ".join(quoted_columns)
        non_null = " AND ".join(f"{column} IS NOT NULL" for column in quoted_columns)
        duplicate_rows = source.execute(
            f"SELECT COALESCE(SUM(group_count - 1), 0) FROM ("
            f"SELECT COUNT(*) AS group_count FROM {quoted_table} "
            f"WHERE {non_null} GROUP BY {columns} HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        if duplicate_rows:
            duplicates[",".join(key)] = duplicate_rows
    return duplicates


def _source_unique_conflicting_row_count(
    source: sqlite3.Connection,
    table_name: str,
    unique_keys: list[tuple[str, ...]],
    primary_key: tuple[str, ...],
) -> int:
    if not unique_keys:
        return 0

    quoted_table = '"' + table_name.replace('"', '""') + '"'
    identity_columns = primary_key or ("rowid",)
    quoted_identity = [
        '"' + column.replace('"', '""') + '"' if column != "rowid" else "rowid"
        for column in identity_columns
    ]
    identity_sql = ", ".join(f'"source_row".{column}' for column in quoted_identity)
    duplicate_groups = []
    for key in unique_keys:
        quoted_columns = ['"' + column.replace('"', '""') + '"' for column in key]
        key_matches = " AND ".join(
            f'"duplicate_row".{column} IS "source_row".{column}'
            for column in quoted_columns
        )
        key_non_null = " AND ".join(
            f'"source_row".{column} IS NOT NULL' for column in quoted_columns
        )
        identity_differs = " OR ".join(
            f'"duplicate_row".{column} IS NOT "source_row".{column}'
            for column in quoted_identity
        )
        duplicate_groups.append(
            f"SELECT {identity_sql} FROM {quoted_table} AS \"source_row\" "
            f"WHERE {key_non_null} AND EXISTS ("
            f"SELECT 1 FROM {quoted_table} AS \"duplicate_row\" "
            f"WHERE {key_matches} AND ({identity_differs}))"
        )
    query = "SELECT COUNT(*) FROM (" + " UNION ".join(duplicate_groups) + ")"
    return int(source.execute(query).fetchone()[0])


def _classify_batch(
    connection: Any,
    table: Table,
    batch: list[dict[str, Any]],
    columns: list[str],
    primary_key: tuple[str, ...],
    unique_keys: list[tuple[str, ...]],
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    source_indexes: dict[tuple[tuple[str, ...], tuple[Any, ...]], list[int]] = defaultdict(list)
    clauses = []
    for key in unique_keys:
        values = set()
        for index, row in enumerate(batch):
            key_value = _key_value(row, key)
            if key_value is not None:
                values.add(key_value)
                source_indexes[(key, key_value)].append(index)
        if not values:
            continue
        if len(key) == 1:
            clauses.append(table.c[key[0]].in_([value[0] for value in values]))
        else:
            clauses.append(tuple_(*(table.c[name] for name in key)).in_(sorted(values, key=repr)))

    matches: list[dict[tuple[Any, ...], dict[str, Any]]] = [dict() for _ in batch]
    matched_keys: list[dict[tuple[Any, ...], set[tuple[str, ...]]]] = [
        dict() for _ in batch
    ]
    if clauses:
        result = connection.execute(select(table).where(or_(*clauses))).mappings()
        for target_row in result:
            existing = dict(target_row)
            target_pk = tuple(existing[name] for name in primary_key)
            for key in unique_keys:
                key_value = _key_value(existing, key)
                if key_value is None:
                    continue
                for index in source_indexes.get((key, key_value), ()):
                    matches[index][target_pk] = existing
                    matched_keys[index].setdefault(target_pk, set()).add(key)

    new_rows = []
    duplicate_count = 0
    conflicts = []
    for index, row in enumerate(batch):
        if not matches[index]:
            new_rows.append(row)
            continue
        source_pk = tuple(row[name] for name in primary_key)
        existing_rows = matches[index]
        if (
            len(existing_rows) == 1
            and source_pk in existing_rows
            and all(existing_rows[source_pk].get(name) == row[name] for name in columns)
        ):
            duplicate_count += 1
            continue
        collisions = []
        for target_pk in existing_rows:
            collisions.append(
                {
                    "target_primary_key": list(target_pk),
                    "unique_keys": [
                        ",".join(key)
                        for key in sorted(matched_keys[index].get(target_pk, set()))
                    ],
                }
            )
        conflicts.append(
            {
                "source_primary_key": list(source_pk),
                "collisions": collisions,
            }
        )
    return new_rows, duplicate_count, conflicts


def _verify_foreign_keys(connection: Any, tables: list[str], schema_name: str | None) -> None:
    inspector = inspect(connection)
    metadata = MetaData()
    table_objects = {
        table_name: Table(table_name, metadata, autoload_with=connection, schema=schema_name)
        for table_name in tables
    }
    for table_name in tables:
        table = table_objects[table_name]
        for foreign_key in inspector.get_foreign_keys(table_name, schema=schema_name):
            referred_name = foreign_key["referred_table"]
            if referred_name not in table_objects:
                continue
            referred = table_objects[referred_name]
            join_conditions = [
                table.c[source_column] == referred.c[target_column]
                for source_column, target_column in zip(
                    foreign_key["constrained_columns"],
                    foreign_key["referred_columns"],
                )
            ]
            non_null = [
                table.c[column].is_not(None)
                for column in foreign_key["constrained_columns"]
            ]
            missing = connection.execute(
                select(table.c[foreign_key["constrained_columns"][0]])
                .select_from(table.outerjoin(referred, and_(*join_conditions)))
                .where(
                    and_(
                        *non_null,
                        referred.c[foreign_key["referred_columns"][0]].is_(None),
                    )
                )
                .limit(1)
            ).first()
            if missing is not None:
                raise RuntimeError(
                    f"Foreign-key verification failed for table {table_name!r}; "
                    "the destination contains an orphaned relationship."
                )


def _synchronize_sequences(connection: Any, tables: list[str], schema_name: str | None) -> None:
    preparer = connection.dialect.identifier_preparer
    schema = schema_name or "public"
    for table in tables:
        table_columns = {column["name"] for column in inspect(connection).get_columns(table)}
        if "id" not in table_columns:
            continue
        qualified_table = preparer.quote_schema(schema) + "." + preparer.quote(table)
        sequence_name = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": qualified_table},
        ).scalar_one_or_none()
        if not sequence_name:
            continue
        sequence_details = connection.execute(
            text(
                "SELECT namespace.nspname, sequence.relname, settings.seqincrement "
                "FROM pg_class AS sequence "
                "JOIN pg_namespace AS namespace ON namespace.oid = sequence.relnamespace "
                "JOIN pg_sequence AS settings ON settings.seqrelid = sequence.oid "
                "WHERE sequence.oid = to_regclass(:sequence_name)"
            ),
            {"sequence_name": sequence_name},
        ).one_or_none()
        if sequence_details is None:
            raise RuntimeError(f"Could not resolve the sequence for table {table!r}.")
        sequence_schema, sequence_relation, increment = sequence_details
        if increment <= 0:
            raise RuntimeError(f"Unsupported non-incrementing PostgreSQL sequence for table {table!r}.")
        qualified_sequence = (
            preparer.quote_schema(sequence_schema) + "." + preparer.quote(sequence_relation)
        )
        sequence_state = connection.execute(
            text(f"SELECT last_value, is_called FROM {qualified_sequence}")
        ).one()
        maximum_id = connection.execute(
            text(f"SELECT MAX({preparer.quote('id')}) FROM {qualified_table}")
        ).scalar_one()
        current_next = sequence_state.last_value + increment if sequence_state.is_called else sequence_state.last_value
        required_next = (maximum_id if maximum_id is not None else 0) + increment
        restart_at = max(current_next, required_next)
        connection.execute(text(f"ALTER SEQUENCE {qualified_sequence} RESTART WITH {restart_at}"))


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


def migrate(
    source_path: str | os.PathLike[str],
    target_engine: Engine,
    *,
    dry_run: bool = False,
    verify_only: bool = False,
    batch_size: int = _BATCH_SIZE,
    backup_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Preview, verify, or transactionally merge a consistent SQLite snapshot."""
    started = time.monotonic()
    report: dict[str, Any] = {
        "tables": {},
        "errors": [],
        "records_examined": 0,
        "records_inserted": 0,
        "records_skipped": 0,
        "records_duplicate": 0,
        "records_conflict": 0,
        "would_insert": 0,
        "source_backup": None,
        "duration_seconds": 0.0,
    }
    source = None
    transaction = None
    try:
        if dry_run and verify_only:
            raise ValueError("--dry-run and --verify cannot be used together.")
        if batch_size < 1:
            raise ValueError("Batch size must be a positive integer.")
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"SQLite source database does not exist: {path}")
        migration_path = path
        if not dry_run and not verify_only:
            backup_path = _create_sqlite_backup(path, backup_dir)
            report["source_backup"] = str(backup_path)
            migration_path = backup_path

        source = sqlite3.connect(_sqlite_uri(migration_path), uri=True)
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        source_error = _source_error(source)
        if source_error:
            raise ValueError(source_error)

        with target_engine.connect() as target:
            if target.dialect.name == "postgresql":
                target = target.execution_options(isolation_level="REPEATABLE READ")
            transaction = target.begin()
            differences, schemas = _validate_schemas(source, target)
            if differences:
                raise ValueError("Database schema validation failed: " + "; ".join(differences))
            target_inspector = inspect(target)
            order = _dependency_order(schemas, target_inspector)
            target_schema = target_inspector.default_schema_name
            target_table_names = set(target_inspector.get_table_names()).difference(_INTERNAL_TABLES)

            if target.dialect.name not in {"postgresql", "sqlite"}:
                raise ValueError("The destination must be PostgreSQL.")
            if target.dialect.name == "postgresql":
                preparer = target.dialect.identifier_preparer
                qualified_schema = (
                    preparer.quote_schema(target_schema) + "."
                    if target_schema
                    else ""
                )
                for table_name in sorted(schemas):
                    target.execute(
                        text(
                            f"LOCK TABLE {qualified_schema}{preparer.quote(table_name)} "
                            "IN SHARE ROW EXCLUSIVE MODE"
                        )
                    )

            metadata = MetaData()
            target_tables = {
                name: Table(name, metadata, autoload_with=target, schema=target_schema)
                for name in order
            }
            insert_function = (
                postgresql_insert if target.dialect.name == "postgresql" else sqlite_insert
            )
            unique_key_sets: dict[str, list[tuple[str, ...]]] = {}
            for table_name in order:
                schema = _target_table_schema(target_inspector, table_name)
                primary_key = tuple(schema["primary_key"])
                if not primary_key:
                    raise ValueError(f"Table {table_name!r} has no primary key.")
                keys = [primary_key]
                keys.extend(
                    tuple(sorted(key))
                    for key in schema["unique_keys"]
                    if tuple(sorted(key)) != primary_key
                )
                unique_key_sets[table_name] = keys

            for table_name in order:
                table = target_tables[table_name]
                primary_key = tuple(schemas[table_name]["primary_key"])
                columns = list(schemas[table_name]["columns"])
                quoted_table = '"' + table_name.replace('"', '""') + '"'
                source_count = source.execute(
                    f"SELECT COUNT(*) FROM {quoted_table}"
                ).fetchone()[0]
                destination_count = target.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
                table_report = {
                    "source_rows": source_count,
                    "destination_rows": destination_count,
                    "examined": 0,
                    "inserted": 0,
                    "duplicates": 0,
                    "conflicts": 0,
                    "skipped": 0,
                    "would_insert": 0,
                    "conflict_examples": [],
                    "source_duplicate_keys": _source_duplicate_key_counts(
                        source,
                        table_name,
                        unique_key_sets[table_name],
                    ),
                    "source_unique_conflicts": _source_unique_conflicting_row_count(
                        source,
                        table_name,
                        unique_key_sets[table_name],
                        primary_key,
                    ),
                    "destination_only": False,
                }
                table_report["conflicts"] = table_report["source_unique_conflicts"]
                table_report["skipped"] = table_report["source_unique_conflicts"]
                report["tables"][table_name] = table_report
                report["records_examined"] += source_count
                source_cursor = source.execute(f"SELECT * FROM {quoted_table}")
                while True:
                    rows = source_cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    converted_batch = [
                        {
                            column: _convert_value(
                                row[column],
                                table.c[column].type,
                            )
                            for column in columns
                        }
                        for row in rows
                    ]
                    table_report["examined"] += len(converted_batch)
                    new_rows, duplicates, conflicts = _classify_batch(
                        target,
                        table,
                        converted_batch,
                        columns,
                        primary_key,
                        unique_key_sets[table_name],
                    )
                    table_report["duplicates"] += duplicates
                    table_report["conflicts"] += len(conflicts)
                    table_report["skipped"] += duplicates + len(conflicts)
                    table_report["would_insert"] += len(new_rows)
                    table_report["conflict_examples"].extend(
                        conflicts[: max(0, 5 - len(table_report["conflict_examples"]))]
                    )
                report["records_duplicate"] += table_report["duplicates"]
                report["records_conflict"] += table_report["conflicts"]
                report["records_skipped"] += table_report["skipped"]
                report["would_insert"] += table_report["would_insert"]

            for table_name in sorted(target_table_names - set(schemas)):
                table = Table(
                    table_name,
                    MetaData(),
                    autoload_with=target,
                    schema=target_schema,
                )
                destination_count = target.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
                report["tables"][table_name] = {
                    "source_rows": 0,
                    "destination_rows": destination_count,
                    "examined": 0,
                    "inserted": 0,
                    "duplicates": 0,
                    "conflicts": 0,
                    "skipped": 0,
                    "would_insert": 0,
                    "conflict_examples": [],
                    "source_duplicate_keys": {},
                    "source_unique_conflicts": 0,
                    "destination_only": True,
                }

            if report["records_conflict"]:
                if not dry_run and not verify_only:
                    raise ValueError(
                        f"Migration aborted without writing data: {report['records_conflict']} "
                        "source rows conflict with existing PostgreSQL records. Review the "
                        "per-table conflict examples before retrying."
                    )
            if verify_only and report["would_insert"]:
                raise ValueError(
                    f"Verification failed: {report['would_insert']} source rows are missing "
                    "from the destination."
                )
            if verify_only and report["records_conflict"]:
                raise ValueError(
                    "Verification failed: destination contains conflicting source records."
                )

            if dry_run or verify_only:
                _verify_foreign_keys(target, sorted(target_table_names), target_schema)
                transaction.rollback()
            else:
                for table_name in order:
                    table = target_tables[table_name]
                    primary_key = tuple(schemas[table_name]["primary_key"])
                    columns = list(schemas[table_name]["columns"])
                    quoted_table = '"' + table_name.replace('"', '""') + '"'
                    source_cursor = source.execute(f"SELECT * FROM {quoted_table}")
                    inserted_for_table = 0
                    while True:
                        rows = source_cursor.fetchmany(batch_size)
                        if not rows:
                            break
                        converted_batch = [
                            {
                                column: _convert_value(
                                    row[column],
                                    table.c[column].type,
                                )
                                for column in columns
                            }
                            for row in rows
                        ]
                        new_rows, _, conflicts = _classify_batch(
                            target,
                            table,
                            converted_batch,
                            columns,
                            primary_key,
                            unique_key_sets[table_name],
                        )
                        if conflicts:
                            raise RuntimeError(
                                f"Destination table {table_name!r} changed after preflight; "
                                "the transaction was rolled back."
                            )
                        if not new_rows:
                            continue
                        statement = (
                            insert_function(table)
                            .values(new_rows)
                            .on_conflict_do_nothing()
                            .returning(*(table.c[name] for name in columns))
                        )
                        inserted_rows = [
                            dict(row._mapping)
                            for row in target.execute(statement).fetchall()
                        ]
                        if len(inserted_rows) != len(new_rows):
                            raise RuntimeError(
                                f"Destination table {table_name!r} changed during insertion; "
                                "the transaction was rolled back."
                            )
                        expected_by_key = {
                            tuple(row[name] for name in primary_key): row
                            for row in new_rows
                        }
                        for inserted_row in inserted_rows:
                            inserted_key = tuple(inserted_row[name] for name in primary_key)
                            expected = expected_by_key.get(inserted_key)
                            if expected is None or any(
                                inserted_row[name] != expected[name] for name in columns
                            ):
                                raise RuntimeError(
                                    f"Imported-value verification failed for table {table_name!r}."
                                )
                        inserted_for_table += len(inserted_rows)
                    table_report = report["tables"][table_name]
                    if inserted_for_table != table_report["would_insert"]:
                        raise RuntimeError(
                            f"Inserted-row verification failed for table {table_name!r}."
                        )
                    current_count = target.execute(
                        select(func.count()).select_from(table)
                    ).scalar_one()
                    if current_count != table_report["destination_rows"] + inserted_for_table:
                        raise RuntimeError(
                            f"Destination row-count verification failed for table {table_name!r}."
                        )
                    table_report["inserted"] = inserted_for_table

                _verify_foreign_keys(target, sorted(target_table_names), target_schema)
                if target.dialect.name == "postgresql":
                    target.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                    changed_tables = [
                        table_name
                        for table_name in order
                        if report["tables"][table_name]["inserted"]
                    ]
                    _synchronize_sequences(target, changed_tables, target_schema)
                transaction.commit()
                report["records_inserted"] = sum(
                    details["inserted"] for details in report["tables"].values()
                )
    except (Exception, KeyboardInterrupt) as error:
        if transaction is not None and transaction.is_active:
            transaction.rollback()
        report["errors"].append(_format_error(error))
        report["records_inserted"] = 0
        for counts in report["tables"].values():
            counts["inserted"] = 0
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


def _print_report(report: dict[str, Any], *, dry_run: bool, verify_only: bool) -> None:
    print("Migration report")
    for table, counts in sorted(report["tables"].items()):
        print(
            f"  {table}: source={counts['source_rows']}, "
            f"destination={counts['destination_rows']}, "
            f"inserted={counts['inserted']}, "
            f"identical_duplicates={counts['duplicates']}, "
            f"conflicts={counts['conflicts']}, "
            f"source_unique_conflicts={counts['source_unique_conflicts']}"
            + (f", would_insert={counts['would_insert']}" if dry_run else "")
            + (", destination_only=true" if counts["destination_only"] else "")
        )
        for key_name, duplicate_count in sorted(counts["source_duplicate_keys"].items()):
            print(f"    source duplicate key {key_name}: {duplicate_count} additional rows")
        for conflict in counts["conflict_examples"]:
            print(
                f"    conflict source_primary_key={conflict['source_primary_key']}, "
                f"collisions={conflict['collisions']}"
            )
    print(f"Tables processed: {len(report['tables'])}")
    print(f"Records examined: {report['records_examined']}")
    print(f"Records inserted: {report['records_inserted']}")
    print(f"Identical records already present: {report['records_duplicate']}")
    print(f"Conflicting source records: {report['records_conflict']}")
    if dry_run:
        print(f"New records that would be inserted: {report['would_insert']}")
    if report["source_backup"]:
        print(f"Verified SQLite source backup: {report['source_backup']}")
    if verify_only and not report["errors"]:
        print("Verification passed: every source row matches a destination row.")
    print(f"Errors: {len(report['errors'])}")
    for error in report["errors"]:
        print(f"  ERROR: {error}", file=sys.stderr)
    print(f"Total migration duration: {report['duration_seconds']:.2f} seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time, repeatable SQLite-to-PostgreSQL data migration."
    )
    parser.add_argument("--source", default="chat.db", help="SQLite file (default: chat.db)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="Read both databases and preview changes without writing either database.",
    )
    modes.add_argument(
        "--verify",
        action="store_true",
        help="Verify schemas, source relationships, destination relationships and row equality.",
    )
    parser.add_argument(
        "--backup-dir",
        help="Directory for the verified SQLite snapshot (default: OPENCHAT_SQLITE_BACKUP_DIR "
        "or ~/OpenChatBackups). Used only for an actual migration.",
    )
    args = parser.parse_args(argv)

    engine = None
    started = time.monotonic()
    try:
        engine = create_engine(_target_url_from_environment(), pool_pre_ping=True)
        report = migrate(
            args.source,
            engine,
            dry_run=args.dry_run,
            verify_only=args.verify,
            backup_dir=args.backup_dir,
        )
    except KeyboardInterrupt:
        report = {
            "tables": {},
            "errors": ["Migration interrupted; the PostgreSQL transaction was rolled back."],
            "records_examined": 0,
            "records_inserted": 0,
            "records_skipped": 0,
            "records_duplicate": 0,
            "records_conflict": 0,
            "would_insert": 0,
            "source_backup": None,
            "duration_seconds": time.monotonic() - started,
        }
    except Exception as error:
        report = {
            "tables": {},
            "errors": [_format_error(error)],
            "records_examined": 0,
            "records_inserted": 0,
            "records_skipped": 0,
            "records_duplicate": 0,
            "records_conflict": 0,
            "would_insert": 0,
            "source_backup": None,
            "duration_seconds": time.monotonic() - started,
        }
    finally:
        if engine is not None:
            engine.dispose()

    _print_report(report, dry_run=args.dry_run, verify_only=args.verify)
    return 1 if report["errors"] or report["records_conflict"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
