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
`sync: false` for credentials and does not provision a Render database or run
migrations automatically. Do not copy URLs or passwords into GitHub, source
files, logs, or issue reports.

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

Render's start command runs `flask --app app db upgrade` and
`flask --app app verify-schema` before Gunicorn starts. Review each revision
before deployment. The upgrade applies only pending revisions; the schema
check confirms all expected application tables and baseline columns are
present. A failed migration or check prevents the web process from
starting.

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

The importer reads SQLite in read-only mode. It preserves primary keys and
password hashes, checks supported columns, reports row counts, source duplicate
keys, and destination key collisions, then refuses to merge into any
non-empty application table. A successful import inserts in dependency order
inside one PostgreSQL transaction, updates identity sequences, and verifies
row counts before commit. It also checks SQLite integrity and foreign-key
consistency before import. A non-dry-run command creates and verifies a
read-only SQLite snapshot before opening the PostgreSQL target; by default the
snapshot is written under `~/OpenChatBackups`, or to the directory passed with
`--backup-dir` or `OPENCHAT_SQLITE_BACKUP_DIR`. Dry runs do not create a
snapshot. The importer does not make a PostgreSQL backup; complete and verify
the encrypted PostgreSQL backup procedure in [BACKUP_SETUP.md](./BACKUP_SETUP.md)
before any real import. No deployment command invokes the importer.

The database contains attachment metadata, but the importer does not copy
attachment bytes from `static/uploads`. Those files need a separate verified
transfer to private object storage before cutover; metadata-only import can
leave links pointing at files unavailable in production. See
[DATABASE_SYNC.md](./DATABASE_SYNC.md) for the proposed, currently inactive
synchronization and object-storage design.

1. Stop or gate writes to the source database. Keep a verified copy of
   `chat.db`.
2. Create an empty Neon branch/database for the migration and apply
   `flask --app app db upgrade` to it. Do not allow application traffic or
   account creation against it yet.
3. From a trusted Windows machine that can reach Neon, load the direct URL from
   the ignored `.env.backup` into this PowerShell process without echoing it:

   ```powershell
   . .\scripts\backup_common.ps1
   $backupConfig = Read-OpenChatBackupConfig
   $env:DATABASE_URL = $backupConfig["PG_DUMP_DATABASE_URL"]
   $env:APP_ENV = "development"
   ```

   Do not paste the URL into a tracked file or share a terminal transcript.
4. Run the read-only preview:

   ```powershell
   flask --app app import-sqlite --dry-run "C:\path\to\chat.db"
   ```

   The command must report zero destination rows, zero source duplicate keys,
   and zero target key collisions. If it reports otherwise, stop. The
   non-empty target policy is intentional; the tool will not merge or replace
   records.
5. After reviewing the preview, execute the transaction:

   ```powershell
   flask --app app import-sqlite "C:\path\to\chat.db"
   ```

6. Review the per-table `source rows`, `verified rows`, and completion report.
   Keep the source backup and do not cut over until account login, password
   verification, admin access, profiles, settings, conversations, groups, and
   messages have been checked.
7. Set Render's `DATABASE_URL` to the Neon **pooled** URL and verify the
   application before opening traffic. Keep `OPENCHAT_DEVELOPER_USER_ID`
   consistent with the preserved account IDs.
8. Remove the process-scoped URL when finished:

   ```powershell
   Remove-Item Env:DATABASE_URL
   ```

The importer does not rewrite user records or passwords. It cannot safely
merge into a Neon database already containing data. Preserve and investigate
that data rather than trying to bypass the empty-target checks.

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
