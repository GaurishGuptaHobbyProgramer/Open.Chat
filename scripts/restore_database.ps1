[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$ConfigPath,
    [switch]$ConfirmOverwrite
)

. (Join-Path $PSScriptRoot "backup_common.ps1")

$plainPath = $null
try {
    $config = Read-OpenChatBackupConfig $ConfigPath
    $settings = Get-OpenChatBackupSettings $config
    if ([string]::IsNullOrWhiteSpace($settings.RestoreDatabaseUrl)) {
        throw "Set RESTORE_DATABASE_URL in .env.backup to the direct URL for a dedicated restore target."
    }

    $resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
    $pgRestorePath = Resolve-OpenChatTool $settings.PgRestorePath "pg_restore"
    $keys = Get-OpenChatEncryptionKeys $settings.KeyPath
    $tempDirectory = Join-Path ([IO.Path]::GetTempPath()) "OpenChatBackupRestore"
    $null = Test-OpenChatEncryptedArchive $resolvedBackup $tempDirectory $pgRestorePath $keys

    $plainPath = Join-Path $tempDirectory ([Guid]::NewGuid().ToString("N") + ".pgdump")
    $null = New-Item -ItemType Directory -Force -Path $tempDirectory
    Read-OpenChatEncryptedArchive $resolvedBackup $plainPath $keys

    $databaseName = Set-OpenChatPostgresEnvironment $settings.RestoreDatabaseUrl -RequireDirect
    $restoreArguments = @(
        "--no-password",
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
        "--single-transaction"
    )
    if ($ConfirmOverwrite) {
        $confirmation = Read-Host "Type the target database name '$databaseName' to replace objects in that database"
        if ($confirmation -cne $databaseName) {
            throw "Restore cancelled; the confirmation did not match the target database name."
        }
        $restoreArguments += @("--clean", "--if-exists")
    } else {
        Write-Output "Safe mode: existing objects will not be dropped. Use a new empty test database."
    }
    $restoreArguments += $plainPath

    & $pgRestorePath @restoreArguments *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore failed with exit code $LASTEXITCODE. No automatic retry or production overwrite was attempted."
    }
    Write-Output "Restore completed to the configured target. Verify table counts and application behavior before cutover."
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    Clear-OpenChatPostgresEnvironment
    if ($plainPath -and (Test-Path -LiteralPath $plainPath)) {
        Remove-Item -LiteralPath $plainPath -Force -ErrorAction SilentlyContinue
    }
    if ($plainPath -and (Test-Path -LiteralPath "$plainPath.cipher.tmp")) {
        Remove-Item -LiteralPath "$plainPath.cipher.tmp" -Force -ErrorAction SilentlyContinue
    }
}
