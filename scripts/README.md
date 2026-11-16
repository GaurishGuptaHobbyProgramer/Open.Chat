# Manual SQLite-to-PostgreSQL migration

[`migrate_sqlite_to_postgres.py`](./migrate_sqlite_to_postgres.py) performs a
manual, repeatable, one-way merge. It never synchronizes continuously. The
destination is never overwritten or reset.

## Configure PostgreSQL

Install dependencies with `python -m pip install -r requirements.txt`. Set
`DATABASE_URL` to the destination Neon PostgreSQL URL from a protected source.
For the local backup configuration file, load its direct PostgreSQL URL
without printing it:

```powershell
. .\scripts\backup_common.ps1
$backupConfig = Read-OpenChatBackupConfig
$env:DATABASE_URL = $backupConfig["PG_DUMP_DATABASE_URL"]
```

The importer requires PostgreSQL and TLS. Do not copy URLs into command
arguments, source files, screenshots, logs, or tickets. Pause local writes to
`chat.db` and schedule the migration while destination writes can be paused;
the importer takes PostgreSQL table locks during its transaction.

## Dry run

From the repository root:

```powershell
python scripts\migrate_sqlite_to_postgres.py --source ".\chat.db" --dry-run
```

This mode performs only reads. It reports per-table source and destination row
counts, identical duplicates, conflicting rows (including duplicate source
keys that would violate destination-only unique constraints), and rows that
would be added. Duplicate-key values are not printed.
It does not create a source backup or write to the destination. Review every
conflict and schema/data error before proceeding. A dry run with conflicts
returns a nonzero exit code.

## PostgreSQL backup

Before a real migration, create and verify the encrypted PostgreSQL backup
described in [`../BACKUP_SETUP.md`](../BACKUP_SETUP.md). For example:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\backup_database.ps1"
$backup = Get-ChildItem "$env:USERPROFILE\OpenChatBackups\openchat-*.pgdump.aes" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
.\scripts\verify_backup.ps1 -BackupPath $backup.FullName
```

The importer independently creates a verified SQLite snapshot before any
actual transfer. It reads from that backup, not from a changing source file.
By default it is stored in `%USERPROFILE%\OpenChatBackups`; choose another
protected location with `--backup-dir` or `OPENCHAT_SQLITE_BACKUP_DIR`.
The original SQLite database is opened read-only and is never changed.

## Actual migration — manual approval required

Only after reviewing the dry run and verifying the PostgreSQL backup, manually
run:

```powershell
python scripts\migrate_sqlite_to_postgres.py --source ".\chat.db" --backup-dir "$env:USERPROFILE\OpenChatBackups"
```

This command is not run by the application or deployment. It preserves source
IDs, password hashes, and relationships. New rows are inserted in foreign-key
order in one PostgreSQL transaction. Identical existing rows are reported and
left unchanged. Any differing primary-key or unique-key collision aborts the
entire transaction; no source rows are silently discarded and no existing
destination row is overwritten. Schema/type mismatches, source integrity or
foreign-key violations, failed row/value/count checks, and sequence errors
also roll back the transaction. PostgreSQL identity sequences are advanced
with transactional `ALTER SEQUENCE ... RESTART`, not non-transactional
`setval`.

The report includes the verified source-backup path, per-table source and
destination counts, inserted/identical/conflicting counts, conflict key
examples (never row values), and errors. Existing target-only tables are
counted and retained. The importer refuses unsupported source tables and
columns rather than dropping their data.

## Verify after migration

Run this read-only check after the import:

```powershell
python scripts\migrate_sqlite_to_postgres.py --source ".\chat.db" --verify
```

It verifies source integrity, schema compatibility, relationships, and that
every source record is present with the same values. It returns nonzero if
records are missing or conflict. You can also use it before import to confirm
that a destination already contains the complete source dataset.

Remove the process-scoped URL when finished:

```powershell
Remove-Item Env:DATABASE_URL
```

Database rows and attachment metadata are transferred, but attachment bytes
under `static/uploads` are not. Plan and verify a separate file transfer if
those files are needed.
