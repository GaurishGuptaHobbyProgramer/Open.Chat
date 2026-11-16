import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Show a read-only SQLite table and row-count inventory."
    )
    parser.add_argument(
        "--source",
        default=str(Path(__file__).resolve().with_name("chat.db")),
        help="Path to the SQLite database (default: chat.db beside this script).",
    )
    args = parser.parse_args(argv)
    database_path = Path(args.source).resolve()
    if not database_path.is_file():
        parser.error(f"SQLite database not found: {database_path}")

    database_uri = "file:" + quote(str(database_path).replace("\\", "/")) + "?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {quick_check}")

        print(f"SQLite quick_check: {quick_check}")
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table_name,) in tables:
            quoted_table = '"' + table_name.replace('"', '""') + '"'
            row_count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_table}"
            ).fetchone()[0]
            print(f"{table_name}: {row_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
