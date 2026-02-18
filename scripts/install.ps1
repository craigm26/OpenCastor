#Requires -Version 5.1
<#
.SYNOPSIS
    OpenCastor Windows 11 Installer
.DESCRIPTION
    Installs OpenCastor on Windows via PowerShell. Uses winget or choco for Python.
.PARAMETER DryRun
    Show what would be installed without making changes.
.PARAMETER SkipWizard
    Skip the interactive setup wizard.
#>
param(
    [switch]$DryRun,
    [switch]$SkipWizard
)

$ErrorActionPreference = "Stop"
$Version = "2026.2.17.14"
$RepoUrl = "https://github.com/craigm26/OpenCastor.git"
$InstallDir = if ($env:OPENCASTOR_DIR) { $env:OPENCASTOR_DIR } else { Join-Path $HOME "opencastor" }

function Write-Step($msg) { Write-Host "`n$msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[ERROR] $msg" -ForegroundColor Red }

# ── Banner ─────────────────────────────────────────────
Write-Host @"

   ___                   ___         _
  / _ \ _ __   ___ _ __ / __|__ _ __| |_ ___ _ _
 | (_) | '_ \ / -_) '_ \ (__/ _`` (_-<  _/ _ \ '_|
  \___/| .__/ \___|_| |_|\___\__,_/__/\__\___/_|
       |_|

  Installer v$Version  |  Windows 11

"@

# ── Check Admin (informational) ───────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warn "Not running as Administrator. Some steps may require elevation."
}

# ── Step 1: Python ────────────────────────────────────
Write-Step "[1/5] Checking Python..."

$python = $null
foreach ($py in @("python3", "python", "py")) {
    try {
        $ver = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) {
            $parts = $ver.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                $python = $py
                break
            }
        }
    } catch {}
}

if (-not $python) {
    Write-Host "Python 3.10+ not found. Attempting install..."
    if (-not $DryRun) {
        $installed = $false
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "Installing via winget..."
            winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            $installed = $true
        } elseif (Get-Command choco -ErrorAction SilentlyContinue) {
            Write-Host "Installing via Chocolatey..."
            choco install python312 -y
            $installed = $true
        }
        if ($installed) {
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
            $python = "python"
        } else {
            Write-Err "Cannot install Python automatically. Install Python 3.10+ from https://python.org and re-run."
            exit 1
        }
    } else {
        Write-Host "[DRY-RUN] Would install Python 3.12 via winget/choco"
        $python = "python"
    }
}
Write-Ok "Using $python ($(& $python --version 2>&1))"

# ── Step 2: Git ───────────────────────────────────────
Write-Step "[2/5] Checking Git..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not $DryRun) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install Git.Git --accept-source-agreements --accept-package-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        } else {
            Write-Err "Git not found. Install from https://git-scm.com and re-run."
            exit 1
        }
    } else {
        Write-Host "[DRY-RUN] Would install Git via winget"
    }
}
Write-Ok "Git found"

# ── Step 3: Clone ────────────────────────────────────
Write-Step "[3/5] Cloning OpenCastor..."
if (Test-Path $InstallDir) {
    Write-Host "Directory exists. Pulling latest..."
    if (-not $DryRun) { git -C $InstallDir pull }
} else {
    if (-not $DryRun) { git clone $RepoUrl $InstallDir }
    else { Write-Host "[DRY-RUN] git clone $RepoUrl $InstallDir" }
}
Set-Location $InstallDir

# ── Step 4: Venv + Install ───────────────────────────
Write-Step "[4/5] Setting up Python environment..."
if (-not $DryRun) {
    & $python -m venv venv
    & .\venv\Scripts\Activate.ps1
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -e ".[core]"
    Write-Ok "Python packages installed"
} else {
    Write-Host "[DRY-RUN] Would create venv and install packages"
}

# ── Step 5: Setup ────────────────────────────────────
Write-Step "[5/5] Setting up..."
if ((Test-Path ".env.example") -and -not (Test-Path ".env")) {
    if (-not $DryRun) { Copy-Item .env.example .env }
    Write-Host "Created .env from template"
}

if (-not $SkipWizard -and -not $DryRun) {
    try { & $python -m castor.wizard } catch { Write-Warn "Wizard skipped. Run 'castor wizard' later." }
}

# ── Done ─────────────────────────────────────────────
Write-Host @"

================================================
  OpenCastor installed successfully!

  Quick Start:
    1. cd $InstallDir
    2. .\venv\Scripts\Activate.ps1
    3. Edit .env and add your ANTHROPIC_API_KEY
    4. castor run --config config\presets\rpi_rc_car.rcan.yaml

  Verify:  powershell -File scripts\install-check.ps1
================================================
"@ -ForegroundColor Green
