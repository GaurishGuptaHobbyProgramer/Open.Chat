import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote


def find_duplicate_user_groups(database_path):
    database_path = Path(database_path).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")

    database_uri = "file:" + quote(str(database_path).replace("\\", "/")) + "?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        rows = connection.execute(
            """
            SELECT lower(trim(username)) AS normalized_username,
                   group_concat(id) AS user_ids,
                   COUNT(*) AS account_count
            FROM users
            WHERE username IS NOT NULL AND trim(username) != ''
            GROUP BY lower(trim(username))
            HAVING COUNT(*) > 1
            ORDER BY lower(trim(username))
            """
        ).fetchall()

    return [
        (normalized_username, sorted(int(user_id) for user_id in user_ids.split(",")), account_count)
        for normalized_username, user_ids, account_count in rows
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report duplicate normalized usernames without modifying the database."
    )
    parser.add_argument(
        "--source",
        default=str(Path(__file__).resolve().with_name("chat.db")),
        help="Path to the SQLite database (default: chat.db beside this script).",
    )
    args = parser.parse_args(argv)

    duplicate_groups = find_duplicate_user_groups(args.source)
    if not duplicate_groups:
        print("No duplicate usernames found. No database rows were changed.")
        return 0

    print(f"Found {len(duplicate_groups)} duplicate username group(s); no rows were changed.")
    for _, user_ids, account_count in duplicate_groups:
        print(f"  accounts={account_count}, user_ids={','.join(map(str, user_ids))}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
