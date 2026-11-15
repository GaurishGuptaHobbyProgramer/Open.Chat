# Database and deployment

Open.Chat uses SQLite (`chat.db`) for local development and Neon PostgreSQL for
production. The application does not create, reset, or migrate the production
schema at web-process startup. Database connections are pooled and scoped to a
Flask application context.

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

Never commit a real connection URL or secret. `.env` is ignored by Git.

## Schema migrations

Install dependencies and apply reviewed migrations before running the web
process:

```powershell
pip install -r requirements.txt
flask --app app db upgrade
```

Render does not run schema migrations automatically. Apply reviewed migrations
manually before directing traffic to a new or changed schema. The initial
migration only creates the schema; it does not drop tables, delete rows, or
seed accounts. Its downgrade intentionally refuses to run. Do not use
`flask db downgrade` as a production recovery procedure.

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

The one-way `import-sqlite` command preserves IDs and password hashes. It opens
the source SQLite file read-only, supports the application's known tables, and
reports source/destination counts and key collisions. Use `--dry-run` first.
The importer refuses non-empty targets, unsupported source tables/columns, or
collisions. Inserts run in one transaction; a failure rolls the target
transaction back. It never updates or replaces existing PostgreSQL data.

1. Stop or gate account creation and other writes while preparing the cutover.
2. Make a separate backup of `chat.db`. For example:

   ```powershell
   $backupDirectory = Join-Path $env:USERPROFILE "OpenChatBackups"
   $null = New-Item -ItemType Directory -Force -Path $backupDirectory
   $env:OPENCHAT_SQLITE_BACKUP = Join-Path $backupDirectory "chat.db.backup"
   python -c "import os,sqlite3; s=sqlite3.connect('chat.db'); d=sqlite3.connect(os.environ['OPENCHAT_SQLITE_BACKUP']); s.backup(d); d.close(); s.close()"
   Get-FileHash -LiteralPath $env:OPENCHAT_SQLITE_BACKUP -Algorithm SHA256
   ```

   Keep this file outside the repository and verify its hash before proceeding.
3. Provision PostgreSQL and apply the baseline migration. Do not serve traffic
   or create accounts in the new database before importing; the importer
   deliberately refuses a non-empty target.
4. From a machine that can reach the PostgreSQL server, set `DATABASE_URL` to
   the destination direct connection URL using a protected environment/secret
   store. First run a read-only preview:

   ```powershell
   flask --app app import-sqlite --dry-run "C:\path\to\chat.db"
   ```

   Proceed only if the preview reports an empty target and no key collisions.
   Then run the same command without `--dry-run` to import. Do not put
   credentials in source files or shared logs.
5. Check the command's per-table row counts and verify sign-in, conversations,
   messages, profiles, and settings before directing users to the new service.
   Keep the SQLite backup until the production data has been independently
   verified.

If an existing PostgreSQL database already has application rows, stop and
review the data. The importer will not merge into or overwrite it.

## Render

Set the Neon pooled URL as Render's `DATABASE_URL`, `APP_ENV=production`,
`SECRET_KEY`, and `OPENCHAT_DEVELOPER_USER_ID` in the Render dashboard.
`render.yaml` marks these values as dashboard-supplied secrets. Migrations and
data import are manual operations. See [DATABASE_MIGRATION.md](./DATABASE_MIGRATION.md),
[BACKUP_SETUP.md](./BACKUP_SETUP.md), and [BACKUP_RESTORE_GUIDE.md](./BACKUP_RESTORE_GUIDE.md)
for cutover, backup, and recovery procedures.
