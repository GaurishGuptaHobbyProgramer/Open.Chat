$script:OpenChatProjectRoot = Split-Path -Parent $PSScriptRoot

function Read-OpenChatBackupConfig {
    param([string]$ConfigPath)

    if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
        $ConfigPath = Join-Path $script:OpenChatProjectRoot ".env.backup"
    }
    $resolvedPath = [Environment]::ExpandEnvironmentVariables($ConfigPath)
    if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
        throw "Backup configuration file not found. Copy .env.backup.example to .env.backup and fill it in."
    }

    $config = @{}
    foreach ($line in [IO.File]::ReadAllLines((Resolve-Path -LiteralPath $resolvedPath).Path)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            throw "Invalid backup configuration line. Expected KEY=value."
        }
        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $config[$name] = [Environment]::ExpandEnvironmentVariables($value)
    }
    return $config
}

function Get-OpenChatBackupSettings {
    param([hashtable]$Config)

    if ([string]::IsNullOrWhiteSpace($Config["BACKUP_ENCRYPTION_KEY_FILE"])) {
        throw "BACKUP_ENCRYPTION_KEY_FILE is required."
    }

    $backupDirectory = $Config["BACKUP_DIR"]
    if ([string]::IsNullOrWhiteSpace($backupDirectory)) {
        $backupDirectory = Join-Path $env:USERPROFILE "OpenChatBackups"
    }
    $backupDirectory = [IO.Path]::GetFullPath($backupDirectory)
    Assert-OpenChatPathOutsideProject $backupDirectory "Backup directory"

    $keyPath = [IO.Path]::GetFullPath($Config["BACKUP_ENCRYPTION_KEY_FILE"])
    Assert-OpenChatPathOutsideProject $keyPath "Encryption key"

    $retentionDays = 30
    if (-not [string]::IsNullOrWhiteSpace($Config["BACKUP_RETENTION_DAYS"])) {
        if (-not [int]::TryParse($Config["BACKUP_RETENTION_DAYS"], [ref]$retentionDays) -or $retentionDays -lt 1) {
            throw "BACKUP_RETENTION_DAYS must be a positive integer."
        }
    }

    $maxAttempts = 3
    if (-not [string]::IsNullOrWhiteSpace($Config["BACKUP_MAX_ATTEMPTS"])) {
        if (-not [int]::TryParse($Config["BACKUP_MAX_ATTEMPTS"], [ref]$maxAttempts) -or $maxAttempts -lt 1 -or $maxAttempts -gt 5) {
            throw "BACKUP_MAX_ATTEMPTS must be between 1 and 5."
        }
    }

    $mirrorDirectory = $Config["BACKUP_MIRROR_DIR"]
    if (-not [string]::IsNullOrWhiteSpace($mirrorDirectory)) {
        $mirrorDirectory = [IO.Path]::GetFullPath($mirrorDirectory)
        Assert-OpenChatPathOutsideProject $mirrorDirectory "Mirror directory"
    }

    return [pscustomobject]@{
        BackupDirectory = $backupDirectory
        KeyPath = $keyPath
        RetentionDays = $retentionDays
        MaxAttempts = $maxAttempts
        MirrorDirectory = $mirrorDirectory
        DatabaseUrl = $Config["PG_DUMP_DATABASE_URL"]
        RestoreDatabaseUrl = $Config["RESTORE_DATABASE_URL"]
        PgDumpPath = $Config["PG_DUMP_PATH"]
        PgRestorePath = $Config["PG_RESTORE_PATH"]
    }
}

function Assert-OpenChatPathOutsideProject {
    param([string]$Path, [string]$Label)

    $project = [IO.Path]::GetFullPath($script:OpenChatProjectRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) +
        [IO.Path]::DirectorySeparatorChar
    $fullPath = [IO.Path]::GetFullPath($Path)
    if ($fullPath.Equals($project.TrimEnd([IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase) -or
        $fullPath.StartsWith($project, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must be outside the Git repository."
    }
}

function Resolve-OpenChatTool {
    param([string]$ConfiguredPath, [string]$CommandName)

    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        $expandedPath = [Environment]::ExpandEnvironmentVariables($ConfiguredPath)
        if (-not (Test-Path -LiteralPath $expandedPath -PathType Leaf)) {
            throw "$CommandName was not found at the configured path."
        }
        return (Resolve-Path -LiteralPath $expandedPath).Path
    }
    $command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$CommandName was not found. Install PostgreSQL client tools or set its path in .env.backup."
    }
    return $command.Source
}

function Get-OpenChatEncryptionKeys {
    param([string]$KeyPath)

    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw "The backup encryption key file was not found."
    }
    try {
        $keyText = [IO.File]::ReadAllText($KeyPath).Trim()
        $keyBytes = [Convert]::FromBase64String($keyText)
    }
    catch {
        throw "The backup encryption key file is invalid."
    }
    if ($keyBytes.Length -ne 64) {
        [Array]::Clear($keyBytes, 0, $keyBytes.Length)
        throw "The backup encryption key must decode to exactly 64 bytes."
    }
    $encryptionKey = New-Object byte[] 32
    $authenticationKey = New-Object byte[] 32
    [Array]::Copy($keyBytes, 0, $encryptionKey, 0, 32)
    [Array]::Copy($keyBytes, 32, $authenticationKey, 0, 32)
    [Array]::Clear($keyBytes, 0, $keyBytes.Length)
    return [pscustomobject]@{
        EncryptionKey = $encryptionKey
        AuthenticationKey = $authenticationKey
    }
}

function Set-OpenChatPostgresEnvironment {
    param([string]$DatabaseUrl, [switch]$RequireDirect)

    try {
        $uri = [Uri]$DatabaseUrl
    }
    catch {
        throw "The configured PostgreSQL URL is invalid."
    }
    if ($uri.Scheme -notin @("postgres", "postgresql") -or
        [string]::IsNullOrWhiteSpace($uri.Host) -or
        [string]::IsNullOrWhiteSpace($uri.UserInfo)) {
        throw "The configured database URL must include PostgreSQL host and credentials."
    }
    if ($RequireDirect -and $uri.Host -match "-pooler(?:\.|$)") {
        throw "Use Neon’s direct, unpooled connection URL for pg_dump and pg_restore."
    }

    $userInfo = $uri.UserInfo
    $separator = $userInfo.IndexOf(":")
    if ($separator -lt 1) {
        throw "The configured PostgreSQL URL must include a username and password."
    }
    $username = [Uri]::UnescapeDataString($userInfo.Substring(0, $separator))
    $password = [Uri]::UnescapeDataString($userInfo.Substring($separator + 1))
    $databaseName = [Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart("/"))
    if ([string]::IsNullOrWhiteSpace($databaseName)) {
        throw "The configured PostgreSQL URL must include a database name."
    }

    $query = @{}
    foreach ($part in $uri.Query.TrimStart("?").Split("&")) {
        if (-not $part) { continue }
        $pair = $part.Split("=", 2)
        $name = [Uri]::UnescapeDataString($pair[0].Replace("+", " "))
        $value = if ($pair.Length -gt 1) {
            [Uri]::UnescapeDataString($pair[1].Replace("+", " "))
        } else {
            ""
        }
        $query[$name.ToLowerInvariant()] = $value
    }

    $sslMode = $query["sslmode"]
    if (-not $sslMode) { $sslMode = "require" }
    if ($sslMode.ToLowerInvariant() -notin @("require", "verify-ca", "verify-full")) {
        throw "PostgreSQL backups and restores require TLS (sslmode=require or stronger)."
    }

    $env:PGHOST = $uri.Host
    $env:PGPORT = if ($uri.Port -gt 0) { [string]$uri.Port } else { "5432" }
    $env:PGUSER = $username
    $env:PGPASSWORD = $password
    $env:PGDATABASE = $databaseName
    $env:PGSSLMODE = $sslMode
    if ($query.ContainsKey("channel_binding")) {
        $env:PGCHANNELBINDING = $query["channel_binding"]
    } else {
        Remove-Item Env:PGCHANNELBINDING -ErrorAction SilentlyContinue
    }
    Remove-Item Env:PGSERVICE,Env:PGSERVICEFILE,Env:PGHOSTADDR -ErrorAction SilentlyContinue
    return $databaseName
}

function Clear-OpenChatPostgresEnvironment {
    Remove-Item Env:PGHOST,Env:PGPORT,Env:PGUSER,Env:PGPASSWORD,Env:PGDATABASE,Env:PGSSLMODE,Env:PGCHANNELBINDING,Env:PGSERVICE,Env:PGSERVICEFILE,Env:PGHOSTADDR -ErrorAction SilentlyContinue
}

function Copy-OpenChatStream {
    param([IO.Stream]$InputStream, [IO.Stream]$OutputStream)

    $buffer = New-Object byte[] 65536
    while (($read = $InputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $OutputStream.Write($buffer, 0, $read)
    }
}

function Get-OpenChatHmac {
    param([string]$Path, [long]$Length, [byte[]]$Key)

    $stream = [IO.File]::OpenRead($Path)
    $hmac = [Security.Cryptography.HMACSHA256]::new([byte[]]$Key)
    try {
        $buffer = New-Object byte[] 65536
        $remaining = $Length
        while ($remaining -gt 0) {
            $read = $stream.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
            if ($read -le 0) { throw "Encrypted backup ended unexpectedly." }
            $null = $hmac.TransformBlock($buffer, 0, $read, $buffer, 0)
            $remaining -= $read
        }
        $empty = New-Object byte[] 0
        $null = $hmac.TransformFinalBlock($empty, 0, 0)
        return ,$hmac.Hash
    }
    finally {
        $stream.Dispose()
        $hmac.Dispose()
    }
}

function Write-OpenChatEncryptedArchive {
    param([string]$InputPath, [string]$OutputPath, $Keys)

    $magic = [Text.Encoding]::ASCII.GetBytes("OCHATB01")
    $aes = [Security.Cryptography.Aes]::Create()
    $input = $null
    $output = $null
    $crypto = $null
    try {
        $aes.KeySize = 256
        $aes.Mode = [Security.Cryptography.CipherMode]::CBC
        $aes.Padding = [Security.Cryptography.PaddingMode]::PKCS7
        $aes.Key = [byte[]]$Keys.EncryptionKey
        $aes.GenerateIV()
        $output = [IO.File]::Open($OutputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $output.Write($magic, 0, $magic.Length)
        $output.Write($aes.IV, 0, $aes.IV.Length)
        $crypto = [Security.Cryptography.CryptoStream]::new(
            $output,
            $aes.CreateEncryptor(),
            [Security.Cryptography.CryptoStreamMode]::Write
        )
        $input = [IO.File]::OpenRead($InputPath)
        Copy-OpenChatStream $input $crypto
        $crypto.FlushFinalBlock()
    }
    finally {
        if ($input) { $input.Dispose() }
        if ($crypto) { $crypto.Dispose() }
        elseif ($output) { $output.Dispose() }
        $aes.Dispose()
    }

    $signedLength = (Get-Item -LiteralPath $OutputPath).Length
    $tag = Get-OpenChatHmac $OutputPath $signedLength $Keys.AuthenticationKey
    $append = [IO.File]::Open($OutputPath, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $append.Write($tag, 0, $tag.Length) }
    finally { $append.Dispose() }
}

function Read-OpenChatEncryptedArchive {
    param([string]$InputPath, [string]$OutputPath, $Keys)

    $magic = [Text.Encoding]::ASCII.GetBytes("OCHATB01")
    $headerLength = 24
    $tagLength = 32
    $cipherPath = "$OutputPath.cipher.tmp"
    $encrypted = [IO.File]::OpenRead($InputPath)
    $cipher = $null
    $aes = [Security.Cryptography.Aes]::Create()
    $crypto = $null
    $plain = $null
    try {
        if ($encrypted.Length -lt ($headerLength + 16 + $tagLength)) {
            throw "Encrypted backup is too short."
        }
        $actualMagic = New-Object byte[] $magic.Length
        $actualIv = New-Object byte[] 16
        if ($encrypted.Read($actualMagic, 0, $actualMagic.Length) -ne $actualMagic.Length -or
            $encrypted.Read($actualIv, 0, $actualIv.Length) -ne $actualIv.Length -or
            [Convert]::ToBase64String($actualMagic) -ne [Convert]::ToBase64String($magic)) {
            throw "Encrypted backup header is invalid."
        }

        $cipherLength = $encrypted.Length - $headerLength - $tagLength
        if ($cipherLength -le 0 -or ($cipherLength % 16) -ne 0) {
            throw "Encrypted backup payload is invalid."
        }
        $expectedTag = New-Object byte[] $tagLength
        $signedLength = $encrypted.Length - $tagLength
        $actualTag = Get-OpenChatHmac $InputPath $signedLength $Keys.AuthenticationKey
        $encrypted.Position = $signedLength
        if ($encrypted.Read($expectedTag, 0, $tagLength) -ne $tagLength) {
            throw "Encrypted backup authentication tag is missing."
        }
        $difference = 0
        for ($index = 0; $index -lt $tagLength; $index++) {
            $difference = $difference -bor ($actualTag[$index] -bxor $expectedTag[$index])
        }
        if ($difference -ne 0) {
            throw "Encrypted backup authentication failed; data was not decrypted."
        }

        $encrypted.Position = $headerLength
        $cipher = [IO.File]::Open($cipherPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $buffer = New-Object byte[] 65536
        $remaining = $cipherLength
        while ($remaining -gt 0) {
            $read = $encrypted.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
            if ($read -le 0) { throw "Encrypted backup payload ended unexpectedly." }
            $cipher.Write($buffer, 0, $read)
            $remaining -= $read
        }
        $cipher.Dispose()
        $cipher = $null

        $aes.KeySize = 256
        $aes.Mode = [Security.Cryptography.CipherMode]::CBC
        $aes.Padding = [Security.Cryptography.PaddingMode]::PKCS7
        $aes.Key = [byte[]]$Keys.EncryptionKey
        $aes.IV = $actualIv
        $cipher = [IO.File]::OpenRead($cipherPath)
        $crypto = [Security.Cryptography.CryptoStream]::new(
            $cipher,
            $aes.CreateDecryptor(),
            [Security.Cryptography.CryptoStreamMode]::Read
        )
        $plain = [IO.File]::Open($OutputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        Copy-OpenChatStream $crypto $plain
    }
    finally {
        if ($plain) { $plain.Dispose() }
        if ($crypto) { $crypto.Dispose() }
        if ($cipher) { $cipher.Dispose() }
        $encrypted.Dispose()
        $aes.Dispose()
        if (Test-Path -LiteralPath $cipherPath) {
            Remove-Item -LiteralPath $cipherPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-OpenChatEncryptedArchive {
    param([string]$BackupPath, [string]$TempDirectory, [string]$PgRestorePath, $Keys)

    $null = New-Item -ItemType Directory -Force -Path $TempDirectory
    $plainPath = Join-Path $TempDirectory ([Guid]::NewGuid().ToString("N") + ".pgdump")
    $listingPath = "$plainPath.list"
    try {
        Read-OpenChatEncryptedArchive $BackupPath $plainPath $Keys
        & $PgRestorePath --list --file $listingPath $plainPath *> $null
        if ($LASTEXITCODE -ne 0 -or
            -not (Test-Path -LiteralPath $listingPath -PathType Leaf) -or
            (Get-Item -LiteralPath $listingPath).Length -eq 0) {
            throw "Decrypted backup is not a readable PostgreSQL custom-format archive."
        }
        return $true
    }
    finally {
        foreach ($path in @($plainPath, $listingPath, "$plainPath.cipher.tmp")) {
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Write-OpenChatBackupLog {
    param([string]$LogPath, [string]$Message, [string]$Level = "INFO")

    $line = "[{0}] {1} {2}" -f [DateTime]::Now.ToString("yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    Write-Output $line
}
