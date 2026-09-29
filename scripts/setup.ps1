# One-time setup for Windows. Run from VS Code (Terminal > Run Task > "Set up project")
# or from PowerShell in the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "    ! $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host ""; Write-Host "Setup stopped: $msg" -ForegroundColor Red; exit 1 }

Step "Checking Python (3.10 or newer)"
$Python = $null
$PythonArgs = @()
foreach ($candidate in @("py -3", "python", "python3")) {
    $parts = $candidate.Split(" ")
    $exe = $parts[0]
    # Note: $parts[1..0] counts DOWN in PowerShell, so build the args explicitly.
    $extra = @(); if ($parts.Length -gt 1) { $extra = $parts[1..($parts.Length - 1)] }
    try {
        $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch { continue }
    if ($LASTEXITCODE -eq 0 -and $version) {
        $version = "$($version | Select-Object -Last 1)".Trim()
        $major, $minor = $version.Split(".") | ForEach-Object { [int]$_ }
        if ($major -eq 3 -and $minor -ge 10) { $Python = $exe; $PythonArgs = $extra; break }
        Warn "Found Python $version via '$candidate', but 3.10+ is required."
    }
}
if (-not $Python) { Fail "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and rerun." }
Write-Host "    Using: $candidate ($version)"

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Step "Creating virtual environment in .venv"
    & $Python @PythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create .venv." }
} else {
    Step "Using existing .venv"
}

Step "Installing dependencies"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install failed. Scroll up for the error." }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "    Created .env"
}

$hasKey = Select-String -Path ".env" -Pattern "^\s*ANTHROPIC_API_KEY\s*=\s*\S+" -Quiet
Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
if (-not $hasKey) {
    Warn "Next: open .env and paste your key after ANTHROPIC_API_KEY= (no quotes needed)."
}
Write-Host "Then press F5 (Run pipeline) in VS Code, or run: .venv\Scripts\python.exe pipeline.py"
