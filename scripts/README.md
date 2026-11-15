# Manual SQLite-to-PostgreSQL migration

[`migrate_sqlite_to_postgres.py`](./migrate_sqlite_to_postgres.py) is a
manual, one-time-at-a-time importer. It is not a synchronization service.
PostgreSQL remains authoritative: matching primary keys and any other unique
conflict are skipped, never updated. New local rows can be imported on a
later run.

## Configure and back up

Install the project's dependencies with
`python -m pip install -r requirements.txt`. Set `DATABASE_URL` in the current
shell to the destination Render PostgreSQL URL; do not put credentials in this
repository or print them in logs. For the existing ignored `.env.backup`
configuration, load it without echoing the secret:

```powershell
. .\scripts\backup_common.ps1
$backupConfig = Read-OpenChatBackupConfig
$env:DATABASE_URL = $backupConfig["PG_DUMP_DATABASE_URL"]
```

Alternatively, supply it from your approved secret manager. The script
requires PostgreSQL and enforces TLS.

Back up PostgreSQL before every real migration, and verify that the backup is
usable. For example, using a direct (unpooled) PostgreSQL URL and an installed
`pg_dump`:

```powershell
pg_dump --format=custom --file "openchat-before-import.dump" $env:DATABASE_URL
```

Keep the backup securely outside the repository. This PostgreSQL backup is
separate from any backup of the SQLite source.

## Preview and run

From the repository root, preview the default `chat.db`:

```powershell
python scripts\migrate_sqlite_to_postgres.py --dry-run
```

The preview validates both schemas and source integrity, attempts the inserts
inside a PostgreSQL transaction, then rolls that transaction back. Review its
projected insert/skip counts and any errors. To use another SQLite file, pass
`--source "C:\path\to\chat.db"`.

After confirming the PostgreSQL backup and preview, run the migration:

```powershell
python scripts\migrate_sqlite_to_postgres.py --source "C:\path\to\chat.db"
```

All transferred rows use their original IDs. Tables are ordered by foreign-key
dependencies, and the PostgreSQL transaction is all-or-nothing. If interrupted
or an insert fails, the transaction rolls back; fix the reported issue and
rerun. PostgreSQL sequences are advanced after explicit IDs are inserted.

## Report

The report lists each table, records examined, inserted and skipped, schema or
data errors, and elapsed time. Dry runs also show how many records would be
inserted. Existing primary keys and other unique-key conflicts count as
skipped; no production row is overwritten. Schema mismatches, invalid source
foreign keys, and insert errors are reported and return a nonzero exit status.
The source is opened read-only and read from a consistent SQLite snapshot.

The script transfers database rows only; it does not copy files referenced by
attachment metadata under `static/uploads`.

Remove the process-scoped URL when finished:

```powershell
Remove-Item Env:DATABASE_URL
```
