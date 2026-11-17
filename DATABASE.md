# Database and deployment

Open.Chat uses SQLite (`chat.db`) for local development and Neon PostgreSQL for
production. Render runs `scripts/start.sh`, which applies pending Alembic
migrations and verifies the PostgreSQL schema before starting Gunicorn as
`wsgi:app`. The WSGI entry point independently verifies the schema before
serving requests.
Database connections are pooled and scoped to a Flask application context.
Automatic database synchronization is not yet implemented; see
[DATABASE_SYNC.md](./DATABASE_SYNC.md) for the proposed design and production
review gates.

## Environment variables

Local development:

```dotenv
APP_ENV=development
DATABASE_URL=chat.db
SECRET_KEY=local-development-only
```

Production must set:

- `APP_ENV=production`
- `DATABASE_URL` to Neon PostgreSQL's pooled connection URL, including
  `sslmode=require`. Configure it as a secret in the Render dashboard.
- `SECRET_KEY` to a stable, secret value. Render generates one in `render.yaml`;
  do not rotate it casually because it invalidates existing sessions.

Production refuses to start without a PostgreSQL `DATABASE_URL`; Render's
`RENDER=true` marker enforces production mode even if `APP_ENV` is misconfigured,
so it cannot silently fall back to local SQLite. Startup logs the sanitized
active endpoint/database/schema, search path, TLS status, and presence of the
principal tables. Use
`python -m flask --app app diagnose-database` in the Render Shell to repeat
these read-only checks and optionally compare a staging account's persisted
conversation and message counts. See
[DATABASE_MIGRATION.md](./DATABASE_MIGRATION.md#diagnosing-sign-outs-or-apparently-missing-data)
for the persistence and session diagnostic procedure.

Authentication uses signed client-side cookies, not worker memory or database
session rows. The configured `SECRET_KEY` must remain identical across restarts
and instances. Production cookies are Secure, HttpOnly, and SameSite=Lax. A
session invalidation requires a sign-in but does not delete the user's database
records.

Never commit a real connection URL or secret. `.env` is ignored by Git.

## Schema migrations

Install dependencies and apply reviewed migrations locally with:

```powershell
pip install -r requirements.txt
flask --app app db upgrade
```

Render's start script runs `python -m flask --app app db upgrade` followed by
`python -m flask --app app verify-schema` on every start. Verification checks
the expected Alembic heads, application tables, columns, primary/unique
constraints, indexes, and foreign keys. A failure prevents Gunicorn from
starting. Review each migration before deployment and keep it
additive/data-preserving. Confirm the Render dashboard has not overridden the
Blueprint start command.

The start script clears Render's injected `GUNICORN_CMD_ARGS` (which includes
`--preload`) and supplies the bind port and access log explicitly. This keeps
Gunicorn from forking workers with an already-created SQLAlchemy connection
pool. The configured worker uses four threads and a per-process bounded pool;
additional Render instances use the same service `DATABASE_URL` and have
separate pools.

The initial migration creates the `users` table. If it is absent in a live
service, the deployed process likely bypassed the checked-in startup path, is
using a different database/schema, or has Alembic-history/schema drift. Check
the effective Render command and database settings before making any change.
Do not stamp, drop, reset, or import SQLite data to fix a missing table.
The initial migration only creates the schema; it does not drop tables, delete
rows, or seed accounts. Its downgrade intentionally refuses to run. Do not use
`flask db downgrade` as a production recovery procedure.

If a PostgreSQL database already has application tables but no Alembic version
record, the upgrade will stop rather than recreate tables or overwrite data.
Compare its schema with the complete baseline first; only then use
`flask --app app db stamp 20261115_01` as a deliberate, manually verified
operation. The automatic Render startup does not stamp databases.

The application currently uses SQL statements rather than SQLAlchemy ORM
models, so Alembic autogeneration cannot reliably infer schema changes. Create
and review explicit Alembic revisions for schema changes. Production revisions
must preserve existing rows; any change that could discard data needs a
separately reviewed, backed-up, manual procedure and must not run as an
automatic deployment migration.

For an existing SQLite database, take a verified backup and compare its schema
with the baseline before using `flask db stamp 20261115_01` to mark it as
migrated. Stamping records a migration version; it does not apply schema
changes. Never stamp a database whose schema has not been checked.

## Copying SQLite data to PostgreSQL

For a manual, repeatable merge that skips identical existing records and
aborts on conflicting keys without overwriting PostgreSQL, use the standalone
[`scripts/migrate_sqlite_to_postgres.py`](./scripts/migrate_sqlite_to_postgres.py).
It validates both schemas and the SQLite source, reads a consistent
read-only snapshot, migrates tables in foreign-key order inside one PostgreSQL
transaction, creates and verifies a source backup before a real run, and
synchronizes PostgreSQL sequences transactionally. Use its `--dry-run` option
first and back up PostgreSQL before every real run. Setup, commands, and report
details are in [`scripts/README.md`](./scripts/README.md).

The `flask --app app import-sqlite` command delegates to the same importer and
conflict policy. Neither command performs automatic synchronization. Back up
PostgreSQL separately before any approved real import.

Both tools transfer database rows only. Attachment metadata may refer to bytes
stored under `static/uploads`; transfer those files separately if their links
need to work in production.

## Render

Set the Neon pooled URL as Render's `DATABASE_URL`, `APP_ENV=production`,
`SECRET_KEY`, and `OPENCHAT_DEVELOPER_USER_ID` in the Render dashboard.
`render.yaml` marks these values as dashboard-supplied secrets. Migrations and
data import are manual operations. See [DATABASE_MIGRATION.md](./DATABASE_MIGRATION.md),
[BACKUP_SETUP.md](./BACKUP_SETUP.md), and [BACKUP_RESTORE_GUIDE.md](./BACKUP_RESTORE_GUIDE.md)
for cutover, backup, and recovery procedures.
