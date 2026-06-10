# Requires PowerShell 5+.
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host "[Setup] $Message" -ForegroundColor Cyan
}

function Stop-Setup {
    param([string]$Message)
    Write-Host "[Setup Error] $Message" -ForegroundColor Red
    exit 1
}

$Candidates = @()
if ($Python) {
    $Candidates += [pscustomobject]@{ Exe = $Python; Args = @() }
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Candidates += [pscustomobject]@{ Exe = "py"; Args = @("-3.11") }
    $Candidates += [pscustomobject]@{ Exe = "py"; Args = @("-3") }
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    $Candidates += [pscustomobject]@{ Exe = "python"; Args = @() }
}

$Selected = $null
foreach ($Candidate in $Candidates) {
    & $Candidate.Exe @($Candidate.Args) -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $Selected = $Candidate
        break
    }
}

if (-not $Selected) {
    Stop-Setup "Python 3.10+ was not found. Install Python 3.11 and run this script again."
}

$PythonLabel = "$($Selected.Exe) $($Selected.Args -join ' ')".Trim()
Write-Step "Project root: $ProjectRoot"
Write-Step "Using Python: $PythonLabel"

Write-Step "Creating virtual environment: .venv"
& $Selected.Exe @($Selected.Args) -m venv ".venv"
if ($LASTEXITCODE -ne 0) {
    Stop-Setup "Failed to create .venv."
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Stop-Setup "Virtual environment Python was not found: $VenvPython"
}

Write-Step "Upgrading pip, setuptools and wheel"
& $VenvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    Stop-Setup "Failed to upgrade pip tooling."
}

Write-Step "Installing project dependencies from requirements.txt"
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Stop-Setup "Dependency installation failed."
}

Write-Step "Checking dependency imports"
& $VenvPython -c "import clickhouse_driver, pandas, numpy, pyarrow, matplotlib, plotly, kaleido, streamlit; print('Dependency import check passed.')"
if ($LASTEXITCODE -ne 0) {
    Stop-Setup "Dependency import check failed."
}

Write-Step "Installation finished."
Write-Host ""
Write-Host "Activate environment:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "Run default backtest:"
Write-Host "  python run_scripts\run_general_multi_ma.py"
Write-Host ""
Write-Host "Run configuration UI:"
Write-Host "  python ui\start_ui.py"
Write-Host ""
Write-Host "If the machine cannot access ClickHouse, set BACKTEST_CH_HOST, BACKTEST_CH_USER and BACKTEST_CH_PASS before running data backtests."
