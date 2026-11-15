[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$ConfigPath
)

. (Join-Path $PSScriptRoot "backup_common.ps1")

try {
    $config = Read-OpenChatBackupConfig $ConfigPath
    $settings = Get-OpenChatBackupSettings $config
    $resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
    $pgRestorePath = Resolve-OpenChatTool $settings.PgRestorePath "pg_restore"
    $keys = Get-OpenChatEncryptionKeys $settings.KeyPath
    $tempDirectory = Join-Path ([IO.Path]::GetTempPath()) "OpenChatBackupVerify"
    $null = Test-OpenChatEncryptedArchive $resolvedBackup $tempDirectory $pgRestorePath $keys
    Write-Output "Verified encrypted backup and PostgreSQL custom archive: $([IO.Path]::GetFileName($resolvedBackup))"
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    Clear-OpenChatPostgresEnvironment
}
