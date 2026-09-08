# ==============================================================================
#  ZOUWUCODE - Quick Launch Installer for Windows
#  Installs the "z" command so you can launch ZOUWUCODE from any directory.
#
#  Usage (run as Administrator):
#    powershell -ExecutionPolicy Bypass -File install.ps1
#
#  After installation, open a NEW terminal and type "z" to start.
# ==============================================================================

# Project root — derived from this script's own location, so the installer
# works from any clone location (no hardcoded paths).
$ZOUWUCODE_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $ZOUWUCODE_DIR "z.ps1"

Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║        ZOUWUCODE Quick Launch Installer         ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "⚠  This script needs Administrator privileges to install the 'z' command." -ForegroundColor Yellow
    Write-Host "   Please run as Administrator:"
    Write-Host "   powershell -ExecutionPolicy Bypass -File install.ps1" -ForegroundColor Gray
    exit 1
}

# Create the PowerShell profile script
$ProfileScript = @"
# ZOUWUCODE Quick Launch
# Added by install.ps1 on $(Get-Date)
function z {
    python "$ZOUWUCODE_DIR\zouwucode\__main__.py" @args
}
function zouwucode {
    python "$ZOUWUCODE_DIR\zouwucode\__main__.py" @args
}
"@

# Install for all users via PowerShell profile
$AllUsersProfile = $PROFILE.AllUsersAllHosts
$ProfileDir = Split-Path $AllUsersProfile -Parent

if (-not (Test-Path $ProfileDir)) {
    New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
}

# Add to profile (check if already exists)
$ExistingContent = ""
if (Test-Path $AllUsersProfile) {
    $ExistingContent = Get-Content $AllUsersProfile -Raw
}

if ($ExistingContent -match "ZOUWUCODE Quick Launch") {
    Write-Host "✓  ZOUWUCODE quick launch is already installed." -ForegroundColor Green
} else {
    Add-Content $AllUsersProfile "`r`n$ProfileScript" -Encoding UTF8
    Write-Host "✓  Installed 'z' and 'zouwucode' commands to PowerShell profile." -ForegroundColor Green
}

# Also create a standalone .ps1 script
$ScriptContent = @"
# ZOUWUCODE Quick Launcher
# Place this script in a PATH directory to use "z" from anywhere

`$ZOUWUCODE_DIR = "$ZOUWUCODE_DIR"
python "`$ZOUWUCODE_DIR\zouwucode\__main__.py" @args
"@

Set-Content -Path $ScriptPath -Value $ScriptContent -Encoding UTF8
Write-Host "✓  Created standalone launcher script: $ScriptPath" -ForegroundColor Green

# Optionally add to PATH
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "Machine")
if ($currentPath -notlike "*$ZOUWUCODE_DIR*") {
    $newPath = "$currentPath;$ZOUWUCODE_DIR"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "Machine")
    Write-Host "✓  Added ZOUWUCODE directory to system PATH." -ForegroundColor Green
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Installation Complete!                         ║" -ForegroundColor Cyan
Write-Host "║                                                  ║" -ForegroundColor Cyan
Write-Host "║  Open a NEW terminal and type:                  ║" -ForegroundColor Cyan
Write-Host "║    z              (quick launch)                ║" -ForegroundColor Cyan
Write-Host "║    z --web        (browser UI)                  ║" -ForegroundColor Cyan
Write-Host "║    z --mode plan  (plan mode)                   ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Cyan