Param(
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[INFO ] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[WARN ] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[ERROR] $msg" -ForegroundColor Red }

$venvPath = Join-Path -Path (Get-Location) -ChildPath ".venv"
$venvPython = Join-Path -Path $venvPath -ChildPath "Scripts/python.exe"
$activateScript = Join-Path -Path $venvPath -ChildPath "Scripts/Activate.ps1"

if (-not (Test-Path $venvPython)) {
    Write-Info "Creating virtual environment at $venvPath"
    python -m venv .venv
}

if (-not (Test-Path $venvPython)) {
    Write-Err "Failed to create virtual environment. Ensure Python is installed and on PATH."
    exit 1
}

Write-Info "Activating virtual environment"
. $activateScript

Write-Info "Upgrading pip"
python -m pip install --upgrade pip

if (Test-Path "requirements.txt") {
    Write-Info "Installing dependencies from requirements.txt"
    pip install -r requirements.txt
} else {
    Write-Warn "requirements.txt not found. Skipping dependency installation."
}

Write-Info "Starting FastAPI app"
$reloadFlag = "--reload"
if ($NoReload) { $reloadFlag = "" }

uvicorn app.main:app $reloadFlag


