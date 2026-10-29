# X-Trace launcher: loads .env + PLAYWRIGHT_BROWSERS_PATH, then forwards args to x-trace.exe
# Usage:
#   .\run-x-trace.ps1 login
#   .\run-x-trace.ps1 run --username RachelDashCS --start 2025-01-01 --end 2025-06-28 --windows 1m,3m

$ErrorActionPreference = "Stop"

# 1. Point Playwright at the project-local browser install (sandbox-safe)
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\x-trace\.playwright_browsers"

# 1b. Route HTTP traffic through Clash (helps yfinance / requests reach Yahoo Finance)
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"

# 2. Load .env (simple key=value parser; skips comments / blanks)
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -le 0) { return }
        $k = $line.Substring(0, $idx).Trim()
        $v = $line.Substring($idx + 1).Trim()
        Set-Item -Path "Env:$k" -Value $v
    }
    Write-Host "[run-x-trace] Loaded .env" -ForegroundColor DarkGray
} else {
    Write-Warning "[run-x-trace] .env not found at $envFile"
}

# 3. Forward all args to the venv-installed CLI
$exe = Join-Path $PSScriptRoot ".venv\Scripts\x-trace.exe"
if (-not (Test-Path $exe)) {
    Write-Error "x-trace.exe not found at $exe. Run 'pip install -e .[dev]' first."
    exit 1
}

& $exe @args
