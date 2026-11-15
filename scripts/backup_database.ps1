[CmdletBinding()]
param([string]$ConfigPath)

. (Join-Path $PSScriptRoot "backup_common.ps1")

$lockStream = $null
$dumpPath = $null
$encryptedStagePath = $null
$finalPath = $null
$finalVerified = $false
$logPath = $null
$settings = $null
$stagingDirectory = $null

try {
    $config = Read-OpenChatBackupConfig $ConfigPath
    $settings = Get-OpenChatBackupSettings $config
    $null = New-Item -ItemType Directory -Force -Path $settings.BackupDirectory
    $logDirectory = Join-Path $settings.BackupDirectory "logs"
    $null = New-Item -ItemType Directory -Force -Path $logDirectory
    $logPath = Join-Path $logDirectory ("backup-{0}.log" -f [DateTime]::Now.ToString("yyyyMMdd"))

    $lockPath = Join-Path $settings.BackupDirectory ".backup.lock"
    try {
        $lockStream = [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    }
    catch [IO.IOException] {
        Write-OpenChatBackupLog $logPath "Skipped because another backup process holds the lock."
        exit 0
    }

    $pgDumpPath = Resolve-OpenChatTool $settings.PgDumpPath "pg_dump"
    $pgRestorePath = Resolve-OpenChatTool $settings.PgRestorePath "pg_restore"
    $keys = Get-OpenChatEncryptionKeys $settings.KeyPath
    if ([string]::IsNullOrWhiteSpace($settings.DatabaseUrl)) {
        throw "PG_DUMP_DATABASE_URL is required and must be the direct, unpooled Neon connection URL."
    }
    $databaseName = Set-OpenChatPostgresEnvironment $settings.DatabaseUrl -RequireDirect
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $finalPath = Join-Path $settings.BackupDirectory "openchat-$stamp.pgdump.aes"
    $stagingRoot = Join-Path ([IO.Path]::GetTempPath()) "OpenChatBackupStaging"
    $stagingDirectory = Join-Path $stagingRoot ([Guid]::NewGuid().ToString("N"))
    Assert-OpenChatPathOutsideProject $stagingDirectory "Temporary staging directory"
    $null = New-Item -ItemType Directory -Force -Path $stagingDirectory
    $dumpPath = Join-Path $stagingDirectory ([Guid]::NewGuid().ToString("N") + ".pgdump")
    $encryptedStagePath = Join-Path $stagingDirectory ([Guid]::NewGuid().ToString("N") + ".partial")

    $dumpSucceeded = $false
    for ($attempt = 1; $attempt -le $settings.MaxAttempts; $attempt++) {
        & $pgDumpPath --no-password --format=custom --file $dumpPath *> $null
        $dumpExitCode = $LASTEXITCODE
        if ($dumpExitCode -eq 0 -and
            (Test-Path -LiteralPath $dumpPath -PathType Leaf) -and
            (Get-Item -LiteralPath $dumpPath).Length -gt 0) {
            $dumpSucceeded = $true
            break
        }

        if (Test-Path -LiteralPath $dumpPath) {
            Remove-Item -LiteralPath $dumpPath -Force -ErrorAction SilentlyContinue
        }
        Write-OpenChatBackupLog $logPath "pg_dump attempt $attempt failed with exit code $dumpExitCode." "WARN"
        if ($attempt -lt $settings.MaxAttempts) {
            Start-Sleep -Seconds ([Math]::Min(30, 5 * $attempt))
        }
    }
    if (-not $dumpSucceeded) {
        throw "pg_dump failed after $($settings.MaxAttempts) attempts. Existing backups were retained."
    }
    Write-OpenChatBackupLog $logPath "Custom-format database dump created; encrypting and verifying."

    Write-OpenChatEncryptedArchive $dumpPath $encryptedStagePath $keys
    $verificationDirectory = Join-Path $stagingDirectory "verify"
    $null = Test-OpenChatEncryptedArchive $encryptedStagePath $verificationDirectory $pgRestorePath $keys
    Move-Item -LiteralPath $encryptedStagePath -Destination $finalPath -ErrorAction Stop
    $encryptedStagePath = $null
    $null = Test-OpenChatEncryptedArchive $finalPath $verificationDirectory $pgRestorePath $keys
    $finalVerified = $true

    if (-not [string]::IsNullOrWhiteSpace($settings.MirrorDirectory)) {
        $null = New-Item -ItemType Directory -Force -Path $settings.MirrorDirectory
        $mirrorPath = Join-Path $settings.MirrorDirectory (Split-Path -Leaf $finalPath)
        Copy-Item -LiteralPath $finalPath -Destination $mirrorPath -ErrorAction Stop
        $localHash = (Get-FileHash -LiteralPath $finalPath -Algorithm SHA256).Hash
        $mirrorHash = (Get-FileHash -LiteralPath $mirrorPath -Algorithm SHA256).Hash
        if ($localHash -ne $mirrorHash) {
            Remove-Item -LiteralPath $mirrorPath -Force -ErrorAction SilentlyContinue
            throw "Encrypted mirror verification failed. The verified local backup was retained."
        }
        Write-OpenChatBackupLog $logPath "Encrypted backup copy verified in the configured mirror directory."
    }

    $cutoff = [DateTime]::UtcNow.AddDays(-$settings.RetentionDays)
    foreach ($oldBackup in Get-ChildItem -LiteralPath $settings.BackupDirectory -Filter "openchat-*.pgdump.aes" -File) {
        if ($oldBackup.FullName -ne $finalPath -and $oldBackup.LastWriteTimeUtc -lt $cutoff) {
            Remove-Item -LiteralPath $oldBackup.FullName -Force -ErrorAction Stop
            Write-OpenChatBackupLog $logPath "Removed verified-expired local backup $($oldBackup.Name)."
        }
    }

    $length = (Get-Item -LiteralPath $finalPath).Length
    Write-OpenChatBackupLog $logPath "Backup verified: $([IO.Path]::GetFileName($finalPath)); $length bytes; retention $($settings.RetentionDays) days."
    exit 0
}
catch {
    if ($logPath) {
        try { Write-OpenChatBackupLog $logPath $_.Exception.Message "ERROR" }
        catch { Write-Error "Backup failed; a backup log could not be written." }
    } else {
        Write-Error $_.Exception.Message
    }
    exit 1
}
finally {
    Clear-OpenChatPostgresEnvironment
    if ($lockStream) { $lockStream.Dispose() }
    foreach ($temporaryPath in @($dumpPath, $encryptedStagePath)) {
        if ($temporaryPath -and (Test-Path -LiteralPath $temporaryPath)) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
    if ($stagingDirectory -and (Test-Path -LiteralPath $stagingDirectory)) {
        Remove-Item -LiteralPath $stagingDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($finalPath -and -not $finalVerified -and (Test-Path -LiteralPath $finalPath)) {
        Remove-Item -LiteralPath $finalPath -Force -ErrorAction SilentlyContinue
    }
}
