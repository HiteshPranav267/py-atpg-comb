#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One-shot setup for a friend running the ATPG/trojan research automation
    on their own Windows laptop. No server, no compiling -- Atalanta.exe is
    already checked into the repo for Windows.

.DESCRIPTION
    Run this from a PowerShell window opened INSIDE the cloned repo folder:

        cd path\to\py-atpg-comb
        .\scripts\friend_setup.ps1

    It checks Python is installed, installs the Python dependencies,
    confirms Atalanta runs, walks you through setting your own LLM API key
    and (optionally) a Discord notification webhook, then offers to start
    the batch run right there.

    Nothing here is destructive: it only installs packages, sets user-level
    environment variables (visible only to your own Windows account), and
    runs the existing automation script. See docs/automation.md for what
    the automation itself actually does.
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    ! $msg" -ForegroundColor Yellow }

# --- 1. Sanity check: are we actually inside the repo? ----------------------
Write-Step "Checking you're running this from inside the repo"
if (-not (Test-Path (Join-Path $repoRoot "requirements.txt"))) {
    Write-Host "Can't find requirements.txt next to scripts\. Run this from the cloned" -ForegroundColor Red
    Write-Host "py-atpg-comb folder: cd path\to\py-atpg-comb ; .\scripts\friend_setup.ps1" -ForegroundColor Red
    exit 1
}
Set-Location $repoRoot
Write-Ok "repo root: $repoRoot"

# --- 2. Python ---------------------------------------------------------------
Write-Step "Checking Python"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python isn't on your PATH." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH'" -ForegroundColor Red
    Write-Host "during install), then re-open PowerShell and run this script again." -ForegroundColor Red
    exit 1
}
$versionOutput = & python --version 2>&1
Write-Ok "$versionOutput ($($python.Source))"

# --- 3. Install Python dependencies -----------------------------------------
Write-Step "Installing Python dependencies (this can take a minute or two)"
& python -m pip install --quiet --upgrade pip
& python -m pip install --quiet -r (Join-Path $repoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed -- scroll up for the actual error." -ForegroundColor Red
    exit 1
}
Write-Ok "dependencies installed"

# --- 4. Confirm Atalanta actually runs ---------------------------------------
Write-Step "Confirming the ATPG engine (Atalanta) runs on this machine"
$atalantaCheck = & python -c "from pyatpg.atpg.binary import resolve_atalanta_binary; b = resolve_atalanta_binary(); print(b.path)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Atalanta could not be located/run:" -ForegroundColor Red
    Write-Host $atalantaCheck -ForegroundColor Red
    Write-Host "On Windows this ships pre-built in the repo (Atalanta\data\Atalanta.exe) --" -ForegroundColor Red
    Write-Host "if it's missing, your download of the repo may be incomplete." -ForegroundColor Red
    exit 1
}
Write-Ok "Atalanta found: $atalantaCheck"

# --- 5. LLM API key -----------------------------------------------------------
Write-Step "LLM API key (for trojan-targeting and ranking -- optional but recommended)"
$existingKeys = @("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NVIDIA_API_KEY", "LLM_API_KEY") |
    Where-Object { [Environment]::GetEnvironmentVariable($_, "User") }
if ($existingKeys) {
    Write-Ok "already set: $($existingKeys -join ', ')"
} else {
    Write-Host "    Get a free key from one of:"
    Write-Host "      Gemini (aistudio.google.com)   - starts with AIza"
    Write-Host "      OpenAI (platform.openai.com)   - starts with sk-"
    Write-Host "      Anthropic (console.anthropic.com) - starts with sk-ant-"
    Write-Host "      NVIDIA NIM (build.nvidia.com)   - starts with nvapi-"
    Write-Host "    Leave this blank to skip -- the tool still runs, using structural"
    Write-Host "    heuristics instead of an LLM."
    $key = Read-Host "    Paste your key (or press Enter to skip)"
    if ($key) {
        [Environment]::SetEnvironmentVariable("LLM_API_KEY", $key, "User")
        $env:LLM_API_KEY = $key  # usable immediately in this session, no reopen needed
        Write-Ok "saved as LLM_API_KEY for your Windows account (the vendor is auto-detected from the key's prefix)"
    } else {
        Write-Warn2 "skipped -- add one later with:"
        Write-Host '      [Environment]::SetEnvironmentVariable("LLM_API_KEY", "your-key", "User")'
    }
}

# --- 6. Discord notifications (optional) -------------------------------------
Write-Step "Discord notifications (optional)"
if ([Environment]::GetEnvironmentVariable("NOTIFY_DISCORD_WEBHOOK", "User")) {
    Write-Ok "already set"
} else {
    Write-Host "    If you want a Discord ping when a circuit needs attention (key ran out,"
    Write-Host "    stuck on errors), paste a webhook URL here. Create one in Discord:"
    Write-Host "    Channel -> Edit Channel -> Integrations -> Webhooks -> New Webhook -> Copy URL."
    $webhook = Read-Host "    Paste webhook URL (or press Enter to skip)"
    if ($webhook) {
        [Environment]::SetEnvironmentVariable("NOTIFY_DISCORD_WEBHOOK", $webhook, "User")
        $env:NOTIFY_DISCORD_WEBHOOK = $webhook
        Write-Ok "saved. Sending a test message..."
        & python -c "from pyatpg.automation.notify import Notifier; Notifier.from_env().notify('Setup complete', 'This laptop is ready to run the automation.', urgent=False)"
    } else {
        Write-Warn2 "skipped -- see docs/automation.md#notifications to add one later"
    }
}

# --- 7. Where to drop circuits ------------------------------------------------
Write-Step "Circuit inbox"
$incoming = Join-Path $repoRoot "benchmarks\incoming"
New-Item -ItemType Directory -Force -Path $incoming | Out-Null
Write-Ok "drop your .v / .verilog / .bench circuit files into: $incoming"

# --- 8. Run it -----------------------------------------------------------------
Write-Step "Ready"
Write-Host "    Setup is done. To process everything currently in benchmarks\incoming\ once:"
Write-Host "      python scripts\run_suite.py research --circuits benchmarks\incoming"
Write-Host ""
Write-Host "    Or to keep this window open and pick up new circuits automatically"
Write-Host "    (what 'just leave your laptop on' means in practice):"
Write-Host "      python scripts\run_suite.py research --circuits benchmarks\incoming --watch 1800"
Write-Host ""
$runNow = Read-Host "Start the watch loop right now? [y/N]"
if ($runNow -eq "y" -or $runNow -eq "Y") {
    & python (Join-Path $repoRoot "scripts\run_suite.py") research --circuits $incoming --watch 1800
}
