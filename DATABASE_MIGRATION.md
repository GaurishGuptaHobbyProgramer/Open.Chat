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

## Diagnosing sign-outs or apparently missing data

Production startup refuses to run unless its configured backend is PostgreSQL.
On Render, the platform's `RENDER=true` marker forces production mode even if
`APP_ENV` is accidentally missing or overridden, so an absent PostgreSQL URL
cannot silently select the ephemeral local `chat.db`.
Application data access uses that selected backend; SQLite connections are
limited to local development/tests and read-only migration source handling.
The checked-in Render start script applies migrations, verifies the active
schema, and starts one Gunicorn worker with four threads. The script clears
Render's injected `GUNICORN_CMD_ARGS` (including `--preload`) and passes the
port explicitly, so Gunicorn does not fork workers with a SQLAlchemy pool
initialized in the parent process. Scaled instances use separate pools but
the same service database URL. Database writes for account creation and
messages are explicitly committed. Connection failures raise errors; the
application does not silently fall back to `chat.db`.

At startup, Render logs a sanitized PostgreSQL identity: endpoint hostname,
database name, current schema, search path, TLS status, and whether the active
search path resolves the users and conversation-message tables. The
read-only diagnostic also reports required-table availability and row counts
for every required application table. The hostname helps distinguish Neon
endpoints/branches; the connection URL and password are not logged. To inspect
the running Render environment without changing data, open the service Shell
and run:

```sh
python -m flask --app app diagnose-database
python -m flask --app app verify-schema
```

For a test account created in a controlled staging environment, the optional
username reports only whether one account matched and its conversation
membership and sent-message counts; it does not print the username:

```sh
python -m flask --app app diagnose-database --username "migration-check-user"
```

Record the database/schema/search-path fields and test-account counts, restart
or redeploy the staging service, then run the same read-only diagnostics again.
If the database identity differs, compare the effective Render `DATABASE_URL`
and service/environment group without displaying or copying the URL. If the
identity is the same but records are absent, investigate database history and
application errors before attempting any recovery. Do not import, reset, stamp,
or restore production data as part of diagnosis.

Authentication uses Flask's signed, client-side cookie session; session state
is not stored in a worker's memory or in PostgreSQL. Login marks the session
permanent, using Flask's default 31-day inactivity lifetime. A process restart
does not invalidate a cookie when the same `SECRET_KEY` is configured, while
changing that key invalidates existing cookies without deleting accounts or
messages. Keep the Render `SECRET_KEY` stable across deploys and all service
instances. In production, cookies are explicitly `Secure`, `HttpOnly`, and
`SameSite=Lax`; no fixed cookie domain is set, so keep users on one canonical
HTTPS hostname. A lost session should require a new sign-in, not make persisted
account data disappear.

For a safe persistence/session reproduction, create a disposable test account
and conversation in staging, then use the read-only diagnostic above before
and after a controlled restart. Confirm the browser remains on the same HTTPS
hostname and that the session cookie is present with `Secure`, `HttpOnly`, and
`SameSite=Lax`. Do not use a production account or destructive operation for
this test.

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
for a manual, repeatable merge into PostgreSQL. It creates and verifies a
SQLite backup before a real run, reads from that backup, preserves IDs and
password hashes, checks source and destination schemas/relationships, and
reports per-table counts, identical duplicates, and conflicts. Identical
records are left unchanged. Any changed primary-key or unique-key collision
aborts the complete import transaction without writing rows; conflicts are
never hidden as generic skips. PostgreSQL sequence synchronization uses
transactional `ALTER SEQUENCE ... RESTART`.

The dry run performs read-only validation and reports prospective insertions
without changing the destination. After manually approving the preview and
verifying a PostgreSQL backup, run the command without `--dry-run`. A later
run imports only genuinely new rows. Use `--verify` to check that every source
row is present with the same values and that destination relationships are
intact. PowerShell setup, dry-run, migration, and verification commands are in
[`scripts/README.md`](./scripts/README.md). No deployment command invokes the
importer.

Destination-only tables and columns with safe defaults are retained. The
importer refuses unsupported source tables/columns instead of silently
discarding them; any such older schema requires a separately reviewed
additive schema migration. The optional `flask --app app import-sqlite`
command delegates to the same importer and conflict policy; neither command
is invoked automatically.

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
