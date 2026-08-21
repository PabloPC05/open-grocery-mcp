[CmdletBinding()]
param(
    [ValidateSet("gadis", "froiz")]
    [string]$Store = "gadis",

    [ValidateSet("automated", "browser-agent")]
    [string]$Mode = "automated",

    [string]$Output = "",

    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Creating local Python environment..."
    py -3.11 -m venv .venv
}

if (-not $SkipInstall) {
    Write-Host "Installing project and browser dependencies..."
    & $Python -m pip install -e ".[dev,browser]"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
    & $Python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "Playwright Chromium installation failed."
    }
}

if ([string]::IsNullOrWhiteSpace($Output)) {
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Output = Join-Path $RepoRoot "local-captures\$Store-authenticated-$Stamp.json"
} elseif (-not [System.IO.Path]::IsPathRooted($Output)) {
    $Output = Join-Path $RepoRoot $Output
}

$OutputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

Write-Host "Store: $Store"
Write-Host "Mode: $Mode"
Write-Host "Capture: $Output"
Write-Host "No order or payment action is permitted."

$CaptureExit = 0
if ($Mode -eq "automated") {
    $env:OPEN_GROCERY_CAPTURE_HEADLESS = "0"
    & $Python .\tools\capture_http_contract.py `
        --store $Store `
        --mode authenticated `
        --output $Output
    $CaptureExit = $LASTEXITCODE
} else {
    Write-Host "The local browser agent must operate the visible window and press Finalizar."
    & $Python .\capture_http_local.py `
        --store $Store `
        --output $Output
    $CaptureExit = $LASTEXITCODE
}

Write-Host "Capture process exit code: $CaptureExit"
Write-Host "Validating observable network data..."

& $Python .\tools\validate_capture.py `
    $Output `
    --minimum-events 5 `
    --require-response
$ValidationExit = $LASTEXITCODE

if ($ValidationExit -ne 0) {
    throw @"
The capture is not valid. The local agent must inspect the JSON, capture code,
page/context listeners and browser behavior, patch the problem and retry. Do
not ask the owner to repeat all phases manually. Capture file: $Output
"@
}

Write-Host "Capture validated successfully: $Output"
Write-Host "The agent may now derive sanitized HTTP fixtures and continue implementation."
