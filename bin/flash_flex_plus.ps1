param(
    [Parameter(Mandatory = $true)]
    [string]$Serial,

    [string]$Password = "",

    [string]$Port = $env:FLEX_SERIAL_PORT,

    [string]$FlashEncryptionKeyFile = "",

    [switch]$SkipSSID
)

$ErrorActionPreference = "Stop"

if (-not $Port) {
    $Port = "COM3"
}

if (-not $Password) {
    if ($env:FLEX_AP_PASSWORD) {
        $Password = $env:FLEX_AP_PASSWORD
    }
    if (-not $Password) {
        $Password = "12345678"
    }
}

function Show-Usage {
    Write-Host "Usage: .\flash_flex_plus.ps1 -Serial <serial> [-Password <softap-password>] [-Port COM3] [--SkipSSID]" -ForegroundColor Yellow
}

function Require-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required file not found: $Path"
    }
}

function Resolve-Python {
    $candidates = @(
        $env:FLEX_PYTHON,
        "python3",
        "python",
        "py"
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            $version = & $command.Source -c "import sys; print(sys.version_info.major)" 2>$null
            if ($version -ge 3) {
                return $command.Source
            }
        } catch {
            continue
        }
    }

    throw "Unable to locate a Python 3 interpreter. Install Python 3 and make sure it is on PATH."
}

function New-TempFilePath([string]$Prefix) {
    $name = "{0}{1}.bin" -f $Prefix, ([System.Guid]::NewGuid().ToString("N"))
    return Join-Path ([System.IO.Path]::GetTempPath()) $name
}

function Sanitize-Serial([string]$Value) {
    $filtered = ($Value.ToCharArray() | Where-Object { $_ -match '[A-Za-z0-9_-]' }) -join ""
    $sanitized = $filtered.Substring(0, [Math]::Min(28, $filtered.Length))
    if (-not $sanitized) {
        throw "Serial suffix must retain at least one valid character after sanitization."
    }
    return $sanitized
}

function Validate-Serial([string]$Value) {
    if (-not $Value -or $Value -notmatch '^[A-Za-z0-9_-]+$') {
        throw "Serial must be alphanumeric and may include _ or -."
    }
}

function Validate-Password([string]$Value) {
    if ($Value.Length -lt 8 -or $Value.Length -gt 63) {
        throw "Password must be between 8 and 63 characters."
    }
    if ($Value.ToCharArray() | Where-Object { [int]$_ -lt 32 -or [int]$_ -gt 126 }) {
        throw "Password must contain printable ASCII characters only."
    }
}

function Resolve-ToolArch([string]$ToolsDir) {
    $preferred = "windows-amd64"
    $alt = "windows-arm64"

    $hostIsArm = $false
    try {
        $archName = (Get-CimInstance Win32_Processor).Name
        if ($archName -match "ARM") { $hostIsArm = $true }
    } catch { }

    if ($hostIsArm) {
        $preferred = "windows-arm64"
        $alt = "windows-amd64"
    }

    $preferredPath = Join-Path $ToolsDir $preferred
    $altPath = Join-Path $ToolsDir $alt

    if (Test-Path $preferredPath) { return $preferred }
    if (Test-Path $altPath) {
        Write-Warning "Toolchain for $preferred not found; falling back to $alt (may require emulation)."
        return $alt
    }
    throw "Neither $preferred nor $alt esptool bundle found under $ToolsDir."
}

function Require-Exe([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required executable not found: $Path"
    }
}

function Get-EfuseSummary([string]$Espefuse, [string]$Port) {
    $output = & $Espefuse --port $Port summary 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read eFuse summary (exit $LASTEXITCODE). Output:`n$output"
    }
    return $output -replace "`r", ""
}

function Needs-FlashEncryptionSetup([string]$Espefuse, [string]$Port) {
    $summary = Get-EfuseSummary -Espefuse $Espefuse -Port $Port
    $line = ($summary -split "`n" | Where-Object { $_ -match "FLASH_CRYPT_CNT" } | Select-Object -First 1)
    if ($line -and $line -match "=\s*0\b") { return $true }
    return $false
}

function Burn-FlashEncryption([string]$Espefuse, [string]$Port, [string]$KeyFile) {
    Write-Host "Burning flash encryption key and eFuses..." -ForegroundColor Cyan
    $burnKeyOutput = "BURN`n" | & $Espefuse --port $Port burn_key flash_encryption $KeyFile 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ($burnKeyOutput -match "read-protected" -or $burnKeyOutput -match "already programmed") {
            Write-Host $burnKeyOutput
            Write-Host "Flash encryption key already programmed; skipping burn_key step." -ForegroundColor Yellow
        } else {
            throw "Failed to burn flash encryption key:`n$burnKeyOutput"
        }
    } else {
        Write-Host $burnKeyOutput
    }

    "BURN`n" | & $Espefuse --port $Port burn_efuse FLASH_CRYPT_CONFIG 0xf
    "BURN`n" | & $Espefuse --port $Port burn_efuse FLASH_CRYPT_CNT 1
    "BURN`n" | & $Espefuse --port $Port burn_efuse DISABLE_DL_DECRYPT 1
    "BURN`n" | & $Espefuse --port $Port burn_efuse DISABLE_DL_CACHE 1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to burn flash encryption eFuses."
    }
    Write-Host "Flash encryption eFuses programmed." -ForegroundColor Green
}

Validate-Serial $Serial
Validate-Password $Password

$SanitizedSerial = Sanitize-Serial $Serial
if ($SanitizedSerial -ne $Serial) {
    Write-Warning ("Serial sanitized to '{0}' for factory config." -f $SanitizedSerial)
    $Serial = $SanitizedSerial
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReleaseDir = Join-Path $ScriptDir "release"
$ToolsRoot = Join-Path (Join-Path $ScriptDir "tools") "esptool"
$ToolArch = Resolve-ToolArch $ToolsRoot
$ToolsDir = Join-Path $ToolsRoot $ToolArch
$FactoryTool = Join-Path (Join-Path $ScriptDir "tools") "gen_factory_payload.py"
$LogDir = Join-Path $ScriptDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Require-File $ReleaseDir
Require-File $FactoryTool

$ManifestPath = Join-Path $ReleaseDir "manifest.json"
Require-File $ManifestPath

$Manifest = Get-Content $ManifestPath | ConvertFrom-Json
$Artifacts = $Manifest.artifacts

$Bootloader = Join-Path $ReleaseDir $Artifacts.bootloader
$BootApp0   = Join-Path $ReleaseDir $Artifacts.boot_app0
$Partitions = Join-Path $ReleaseDir $Artifacts.partitions
$Firmware   = Join-Path $ReleaseDir $Artifacts.firmware
$Spiffs     = Join-Path $ReleaseDir $Artifacts.spiffs
$FactoryTemplate = Join-Path $ReleaseDir $Artifacts.factory_cfg

[$Bootloader, $BootApp0, $Partitions, $Firmware, $Spiffs, $FactoryTemplate] | ForEach-Object {
    Require-File $_
}

$EsptoolPath   = Join-Path $ToolsDir "esptool.exe"
$EspefusePath  = Join-Path $ToolsDir "espefuse.exe"
$EspsecurePath = Join-Path $ToolsDir "espsecure.exe"
Require-Exe $EsptoolPath
Require-Exe $EspefusePath
Require-Exe $EspsecurePath

$PythonExe = Resolve-Python

$FactoryPlainPath = New-TempFilePath "factorycfg_plain_"
$factoryArgs = @(
    $FactoryTool,
    "--serial", $SanitizedSerial,
    "--password", $Password,
    "--output", $FactoryPlainPath
)
& $PythonExe @factoryArgs

$EncryptionEnabled = $Manifest.flash_encryption -eq "enabled"
if (-not $EncryptionEnabled) {
    throw "Manifest flash_encryption is 'disabled'; production flashing requires encrypted bundles."
}
if (-not $FlashEncryptionKeyFile) {
    $FlashEncryptionKeyFile = Join-Path $ScriptDir "keys/flash_encryption_key.bin"
}
Require-File $FlashEncryptionKeyFile

if (Needs-FlashEncryptionSetup -Espefuse $EspefusePath -Port $Port) {
    Burn-FlashEncryption -Espefuse $EspefusePath -Port $Port -KeyFile $FlashEncryptionKeyFile
} else {
    Write-Host "Flash encryption already enabled on target." -ForegroundColor Green
}

$FactoryFlashPath = New-TempFilePath "factorycfg_enc_"
$espsecureArgs = @(
    "encrypt_flash_data",
    "--keyfile", $FlashEncryptionKeyFile,
    "--address", "0x3F0000",
    "--output", $FactoryFlashPath,
    $FactoryPlainPath
)
& $EspsecurePath @espsecureArgs

$UsePreEncrypted = ($FactoryTemplate -like "*.enc.*") -or ($Bootloader -like "*.enc.*")
$CompressionArg = if ($UsePreEncrypted) { "--no-compress" } else { "--encrypt" }
$FlashBaud = if ($env:FLEX_FLASH_BAUD) { $env:FLEX_FLASH_BAUD } else { "921600" }

$flashArgs = @(
    "--chip", "esp32",
    "--port", $Port,
    "--baud", $FlashBaud,
    "--before", "default_reset",
    "--after", "hard_reset",
    "write_flash",
    $CompressionArg,
    "--flash_mode", "dio",
    "--flash_freq", "40m",
    "--flash_size", "detect",
    "0x1000", $Bootloader,
    "0x8000", $Partitions,
    "0xE000", $BootApp0,
    "0x10000", $Firmware,
    "0x290000", $Spiffs,
    "0x3F0000", $FactoryFlashPath
)

Write-Host "Flashing $($Manifest.version) to $Port" -ForegroundColor Cyan

$flashStatus = "failed"
try {
    & $EsptoolPath @flashArgs
    Write-Host "Flash complete." -ForegroundColor Green
    $flashStatus = "wired_only"
} finally {
    if (Test-Path $FactoryPlainPath) {
        Remove-Item $FactoryPlainPath -ErrorAction SilentlyContinue
    }
    if ($FactoryFlashPath -and (Test-Path $FactoryFlashPath) -and $FactoryFlashPath -ne $FactoryPlainPath) {
        Remove-Item $FactoryFlashPath -ErrorAction SilentlyContinue
    }
    $logLine = "{0},{1},{2},{3}`n" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"), $Serial, (Split-Path $ReleaseDir -Leaf), $flashStatus
    Add-Content -Path (Join-Path $LogDir "flash_log.csv") -Value $logLine
}

if (-not $SkipSSID) {
    Write-Warning "SSID provisioning not implemented on Windows yet."
}
