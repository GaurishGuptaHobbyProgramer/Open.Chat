# Backup verification and restore procedure

## Verify an encrypted backup

`verify_backup.ps1` authenticates the AES-256-CBC/HMAC-SHA256 envelope before
decrypting to a temporary file. It then asks `pg_restore --list` to inspect the
PostgreSQL custom archive. It does not connect to a database or print
credentials.

```powershell
$backup = Get-ChildItem "$env:USERPROFILE\OpenChatBackups\openchat-*.pgdump.aes" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
.\scripts\verify_backup.ps1 -BackupPath $backup.FullName
```

Verification requires the correct key and a compatible `pg_restore`. A
successful verification proves the encrypted bytes authenticate and the
decrypted file is parseable as a PostgreSQL archive; it does not prove a
database restore has completed.

## Restore to a separate test database first

1. In Neon, create a **separate empty test branch/database**. Do not use the
   production URL for this test.
2. Set `RESTORE_DATABASE_URL` in the ignored `.env.backup` file to that test
   database's direct/unpooled URL with `sslmode=require`.
3. Verify the backup:

   ```powershell
   .\scripts\verify_backup.ps1 -BackupPath "C:\Users\<you>\OpenChatBackups\openchat-<UTC>.pgdump.aes"
   ```

4. Restore without overwrite permission:

   ```powershell
   .\scripts\restore_database.ps1 -BackupPath "C:\Users\<you>\OpenChatBackups\openchat-<UTC>.pgdump.aes"
   ```

   This uses `pg_restore --single-transaction`. It does not drop existing
   objects; if names collide, the restore fails rather than cleaning the
   database. Use a fresh empty database for the first restore test.
5. In Neon SQL Editor (or with `psql` connected to the test database), compare
   row counts and relationships with the expected backup/source counts. For
   example:

   ```sql
   SELECT 'users' AS table_name, COUNT(*) FROM users
   UNION ALL SELECT 'user_profiles', COUNT(*) FROM user_profiles
   UNION ALL SELECT 'conversations', COUNT(*) FROM conversations
   UNION ALL SELECT 'conversation_members', COUNT(*) FROM conversation_members
   UNION ALL SELECT 'conversation_messages', COUNT(*) FROM conversation_messages
   UNION ALL SELECT 'messages', COUNT(*) FROM messages
   UNION ALL SELECT 'user_settings', COUNT(*) FROM user_settings
   UNION ALL SELECT 'user_notifications', COUNT(*) FROM user_notifications;
   ```

   Check representative foreign-key relationships for orphans:

   ```sql
   SELECT COUNT(*) AS orphan_members
   FROM conversation_members cm
   LEFT JOIN conversations c ON c.id = cm.conversation_id
   LEFT JOIN users u ON u.id = cm.user_id
   WHERE c.id IS NULL OR u.id IS NULL;

   SELECT COUNT(*) AS orphan_messages
   FROM conversation_messages m
   LEFT JOIN conversations c ON c.id = m.conversation_id
   LEFT JOIN users u ON u.id = m.sender_id
   WHERE c.id IS NULL OR u.id IS NULL;
   ```

   Both counts should be zero. Then test sign-in/password verification, admin
   access, profiles, settings, direct/group conversations, and message history
   in a test deployment pointed only at this test database.
6. Record the test result and retain the original backup and encryption key.
   Do not change production `DATABASE_URL` until the test restore is reviewed.

## Explicitly overwrite database objects

Overwriting is never the default. Use `-ConfirmOverwrite` only for an
intentional, reviewed recovery. The script first verifies the encrypted archive,
then asks you to type the exact target database name. It passes
`--clean --if-exists --single-transaction` to `pg_restore`; matching objects
from the archive are dropped and recreated. This is destructive to those
objects.

```powershell
.\scripts\restore_database.ps1 `
  -BackupPath "C:\Users\<you>\OpenChatBackups\openchat-<UTC>.pgdump.aes" `
  -ConfirmOverwrite
```

The target URL still comes only from `.env.backup`; it is not a command-line
argument. Never use this mode as an automatic deployment action. Prefer a
separate test target, verify counts and application behavior, and obtain
explicit approval before any production recovery.

## Recovery checklist

- Stop or gate application writes before any production recovery.
- Select a verified encrypted archive and confirm the matching encryption key
  is available. If the key is lost, the backup cannot be decrypted.
- Restore to a separate test database first using the non-overwrite command.
- Compare table counts, relationships, and account/password behavior with the
  expected recovery point.
- Only after approval, decide whether production should be restored using the
  explicit overwrite mode or whether a new Neon database should be cut over.
- Preserve the failed/current production database and the pre-recovery backup
  until the recovery is independently verified.
- If an application release caused a schema issue, do not use
  `flask db downgrade` as a data-recovery shortcut. Apply a reviewed forward
  fix or use a separately restored database.

The backup scripts were locally tested for encrypted round-trip and tamper
rejection. A real PostgreSQL restore must still be tested in your Neon test
database; do not treat this guide or archive-list verification as proof that a
production restore has been exercised.
