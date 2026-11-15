# Encrypted PostgreSQL backups on Windows

The scripts use `pg_dump` custom archives, AES-256-CBC encryption with
HMAC-SHA256 authentication, and `pg_restore --list` verification. The HMAC is
checked before decryption. Backup files and logs live outside the repository.
No database URL or encryption key is written to a log or passed as a command
line argument to PostgreSQL tools.

## 1. Install PostgreSQL client tools

Install the PostgreSQL command-line tools for Windows. Use a client version
compatible with the Neon server version. Ensure `pg_dump` and `pg_restore` are
on `PATH`, or set `PG_DUMP_PATH` and `PG_RESTORE_PATH` in `.env.backup`.
The backup cannot run until those tools are available.

## 2. Create and protect an encryption key

The 64 random bytes below form separate 256-bit encryption and authentication
keys. The key must remain available for the lifetime of every backup encrypted
with it. Losing it makes those backups unrecoverable.

Run this in PowerShell. It writes the key outside the repository and does not
print it:

```powershell
$secretDir = Join-Path $env:USERPROFILE "OpenChatSecrets"
$null = New-Item -ItemType Directory -Force -Path $secretDir
$keyPath = Join-Path $secretDir "openchat.backup.key"
$keyBytes = New-Object byte[] 64
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($keyBytes)
[IO.File]::WriteAllText($keyPath, [Convert]::ToBase64String($keyBytes))
[Array]::Clear($keyBytes, 0, $keyBytes.Length)
$rng.Dispose()
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $secretDir /inheritance:r /grant:r "*${sid}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F"
icacls $keyPath /inheritance:r /grant:r "*${sid}:R" "*S-1-5-18:F"
```

Keep a separately protected copy of the key (for example, in an organization
password manager or encrypted offline storage). Do not put it in GitHub,
Render, a public cloud folder, or the same backup directory as the archives.

## 3. Configure local secrets

Copy the safe template, then edit the ignored local file:

```powershell
Copy-Item .env.backup.example .env.backup
notepad .env.backup
```

Set:

- `PG_DUMP_DATABASE_URL` to Neon’s **direct/unpooled** PostgreSQL URL with
  `sslmode=require`.
- `BACKUP_ENCRYPTION_KEY_FILE` to the key path created above.
- `BACKUP_DIR` to `%USERPROFILE%\OpenChatBackups` or another directory outside
  the repository.
- `BACKUP_RETENTION_DAYS` to the desired retention; the default is 30.
- `BACKUP_MAX_ATTEMPTS` to 1-5; the default is 3.
- `PG_DUMP_PATH` and `PG_RESTORE_PATH` only if the tools are not on `PATH`.
- `BACKUP_MIRROR_DIR` optionally to a private cloud-synced directory.

The application uses Neon’s **pooled** `DATABASE_URL`; these scripts use a
separate direct URL for backup/restore. `.env.backup` is Git-ignored. Check
`git status --ignored --short .env.backup` before putting any credentials in
it. Never include a connection URL in command arguments, screenshots, logs,
or support tickets.

## 4. Run and verify a backup manually

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\backup_database.ps1"
```

The script:

1. Obtains a non-overlap lock.
2. Calls `pg_dump --format=custom` using libpq environment variables, not a
   command-line URL.
3. Retries failed `pg_dump` attempts at most the configured number of times.
4. Encrypts the archive into a timestamped `openchat-<UTC>.pgdump.aes` file.
5. Authenticates/decrypts it and requires `pg_restore --list` to inspect it.
6. Publishes only a verified file, optionally verifies a byte-identical
   encrypted mirror, then removes expired local archives.

Unencrypted working files are created only in the Windows temporary directory
and are removed on normal completion/failure. Protect the Windows account and
its temporary storage; a sudden power loss can leave a temporary file behind.
The encrypted archive is the only long-term backup artifact.

Inspect the newest backup path without printing secret configuration:

```powershell
$backup = Get-ChildItem "$env:USERPROFILE\OpenChatBackups\openchat-*.pgdump.aes" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
.\scripts\verify_backup.ps1 -BackupPath $backup.FullName
```

Logs are in `BACKUP_DIR\logs`. Failures return a non-zero exit code; the script
does not delete older valid backups after a failed or unverified run. Retention
cleanup happens only after a newer backup passes encrypted-file and
`pg_restore --list` verification.

## 5. Optional private cloud-synced copy

Set `BACKUP_MIRROR_DIR` to a folder synced by a **private** cloud account. The
script copies only the verified encrypted `.pgdump.aes` file and compares its
SHA-256 hash with the local archive. Never sync the temporary directory, key,
`.env.backup`, or a plaintext `.pgdump`. Verify the cloud provider's account
access, MFA, deletion/version history, and recovery policy separately.

## 6. Schedule daily at 2:00 AM

The PC must be powered on, awake, and connected to the internet when the task
runs. A scheduled backup cannot run while it is off or disconnected.

In Task Scheduler:

1. Select **Create Task**.
2. On **General**, use the Windows account that can read `.env.backup` and the
   encryption key. Select **Run whether user is logged on or not** if desired;
   Windows will request that account's password.
3. On **Triggers**, create a daily trigger at **2:00 AM local time**. Adjust
   the time as needed.
4. On **Actions**, select **Start a program**:
   - Program: `powershell.exe`
   - Arguments:
     ```text
     -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\path\to\OpenChat\scripts\backup_database.ps1"
     ```
   - Start in: the repository root.
5. On **Settings**, choose **If the task is already running: Do not start a
   new instance**. Enable **Start the task as soon as possible after a
   scheduled start is missed**. Optionally configure two scheduler-level
   retries; the script itself already retries `pg_dump` up to three times.
6. Save, enter the Windows account password if requested, then use **Run** and
   verify the encrypted archive before relying on the schedule.

The script's lock prevents overlapping runs even if another scheduler or
manual invocation starts at the same time. Review `BACKUP_DIR\logs` and Task
Scheduler's **History** regularly. For recovery steps, see
[BACKUP_RESTORE_GUIDE.md](./BACKUP_RESTORE_GUIDE.md).
