# Database migration and Neon configuration

## Current database architecture

The application has no pre-existing ORM model layer; database access is raw
SQL in `app.py`. The version-controlled Alembic baseline represents all 16
existing application tables and their foreign keys, composite/unique keys, and
indexes. Flask-Migrate tracks schema revision history. SQLAlchemy supplies a
pre-pinged PostgreSQL connection pool; application connections are checked out
per Flask context and returned during teardown.

Local development continues to use `chat.db`. Production must use PostgreSQL;
startup rejects SQLite when `APP_ENV=production`. The app adds
`sslmode=require` if the PostgreSQL URL does not specify TLS, rejects weaker
SSL modes, and uses a small bounded connection pool for Neon.

## Neon and Render variables

In Neon, copy the **pooled** connection string for the production application.
It normally has a `-pooler` hostname. In Render, set:

| Variable | Value |
| --- | --- |
| `APP_ENV` | `production` |
| `DATABASE_URL` | Neon pooled PostgreSQL URL, with `sslmode=require` |
| `SECRET_KEY` | Stable, private Flask signing key |
| `OPENCHAT_DEVELOPER_USER_ID` | Existing admin/developer user ID(s), if applicable |

Use Render's secret/environment-variable UI. `render.yaml` intentionally uses
`sync: false` for credentials and does not provision a Render database. Its
start command runs `scripts/start.sh`, which applies pending migrations and
verifies the schema before starting Gunicorn. The Render dashboard can override
the Blueprint start command; confirm that its configured command still runs
this script. Do not copy URLs or passwords into GitHub, source files, logs, or
issue reports.

Neon's pooled URL is appropriate for normal request traffic. Use Neon’s
**direct, unpooled** URL for `pg_dump`, `pg_restore`, and restore/migration
maintenance. The backup scripts reject pooled endpoints for those operations.
The direct URL belongs only in the ignored local `.env.backup` file.

## Applying and tracking schema migrations

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

For a new empty local or Neon database, apply the initial schema explicitly:

```powershell
flask --app app db upgrade
flask --app app db current
flask --app app db history
```

Render's start command runs `scripts/start.sh`. It invokes
`python -m flask --app app db upgrade`, then
`python -m flask --app app verify-schema`, and only then executes Gunicorn as
`wsgi:app`. The WSGI import independently verifies the PostgreSQL schema before
serving requests. Review each revision before deployment. Verification checks
the expected Alembic heads, application tables, columns, primary/unique
constraints, indexes, and foreign keys. A failed migration or check exits
non-zero and prevents the web process from starting.

The initial revision `20261115_01` creates `users` first and includes the
other 15 application tables, their keys, foreign keys, and three explicit
indexes. If Render serves requests while `users` is missing, the running
release is not using the checked-in startup path, is connected to a different
schema/database than expected, or has migration-history/schema drift. Check
Render's effective start command and secret/environment group, then inspect
`flask db current` and the schema in a separate reviewed session. A revision
record alone does not prove that tables exist. Do not stamp, drop, reset, or
import data to address this error.

If the database already contains application tables but has no Alembic version
record, the upgrade will fail safely rather than recreate tables or overwrite
data. Compare the database schema with the complete baseline before manually
stamping it. Render startup never stamps a database.

The app's queries are not represented as SQLAlchemy ORM models, so Alembic
autogeneration does not know the complete schema. Create explicit revisions
under `migrations/versions/` and use `op` operations. Review generated SQL
against all existing table relationships. Do not add destructive operations
to an automated deployment; changes that remove or rewrite data require a
separate, backed-up, manually confirmed procedure.

The current app has no separate roles/permissions tables: developer/admin
access is controlled by `OPENCHAT_DEVELOPER_USER_ID`. The importer retains
account IDs, so configure the same authorized ID(s) in Render after import.
Do not grant admin access by changing password hashes or copying IDs from a
different database.

For a legacy database that already has the complete baseline schema but no
Alembic version table, inspect and compare every table, column, constraint, and
index first. Only then mark the exact baseline as applied:

```powershell
flask --app app db stamp 20261115_01
```

`stamp` records history; it does not apply or validate schema changes. Never
stamp an unverified database. If `upgrade` reports that a relation already
exists, stop and inspect the database rather than dropping it or stamping it
blindly.

## Safe SQLite-to-PostgreSQL transfer

Use the standalone
[`scripts/migrate_sqlite_to_postgres.py`](./scripts/migrate_sqlite_to_postgres.py)
for a manual, repeatable merge into PostgreSQL. It preserves IDs and source
values, skips primary-key and other unique-key conflicts, validates source and
destination schemas, uses a consistent read-only SQLite snapshot, and applies
all inserts in one transaction. A failed or interrupted run rolls back and can
be retried. PostgreSQL sequences are advanced after explicit IDs are inserted.

Before each real run, make and verify a PostgreSQL backup. Set `DATABASE_URL`
in the current shell without storing or logging the credential. Run
`python scripts\migrate_sqlite_to_postgres.py --dry-run` first, review the
per-table preview, then execute without `--dry-run`. Full instructions and
report interpretation are in [`scripts/README.md`](./scripts/README.md).
The older `flask --app app import-sqlite` command remains empty-target-only;
it must not be used to merge into populated PostgreSQL. No deployment command
automatically invokes either importer.

The transfer includes database rows and attachment metadata, not file bytes
under `static/uploads`. A separate verified file transfer may be needed.
See [DATABASE_SYNC.md](./DATABASE_SYNC.md) for the inactive synchronization
proposal; this importer is not continuous synchronization.

## Rollback and recovery

The initial migration intentionally refuses automatic downgrade because that
would remove application data. For an additive schema change, rolling back the
application code while retaining the additive schema is generally safer than
running `flask db downgrade`. Use a reviewed forward migration to correct
schema mistakes.

For data recovery, restore a verified backup into a **separate empty test
database** first, validate counts and application behavior, and only then
plan a manually approved production recovery. Never point a restore command at
production as a routine rollback. See [BACKUP_RESTORE_GUIDE.md](./BACKUP_RESTORE_GUIDE.md).
