# GroundedOps installer - Windows
# Double-click install.cmd, or run:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Keep this file pure ASCII. A non-ASCII character (em dash, curly quote)
# in a UTF-8 file without a BOM is decoded as ANSI by Windows PowerShell
# 5.1, where byte 0x94 becomes a closing smart quote and silently
# terminates a string - producing a parse error pointing at the wrong line.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Say($msg, $colour = "White") { Write-Host $msg -ForegroundColor $colour }

Say ""
Say "  GroundedOps installer" Cyan
Say "  =====================" Cyan
Say ""

# --- Locate the application code -------------------------------------
# The launcher scripts sit at the repository root, but the Python package
# lives in src/. Look in both so the scripts work either way.
$app = $null
foreach ($candidate in @($root, (Join-Path $root "src"))) {
    if (Test-Path (Join-Path $candidate "requirements.txt")) { $app = $candidate; break }
}
if (-not $app) {
    Say "Could not find requirements.txt in this folder or in src\." Red
    Say "Run this script from the folder you unzipped or cloned into." Red
    Read-Host "Press Enter to exit"
    exit 1
}
Say "Application folder: $app" DarkGray

# --- 1. Python -------------------------------------------------------
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $v = & $cmd --version 2>&1
        if ($v -match "Python 3\.(1[1-9]|[2-9]\d)") { $python = $cmd; Say "Found $v" Green; break }
    } catch {}
}

if (-not $python) {
    Say "Python 3.11+ not found." Yellow
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Say "Installing Python 3.12 (a few minutes)..." Cyan
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        Say ""
        Say "Python installed. CLOSE THIS WINDOW and run install.cmd again" Yellow
        Say "so Windows picks up the new PATH." Yellow
        Read-Host "Press Enter to exit"
        exit 0
    }
    Say "Install Python 3.11+ (tick 'Add Python to PATH'), then re-run:" Red
    Say "  https://www.python.org/downloads/" Red
    Read-Host "Press Enter to exit"
    exit 1
}

# --- 2. Virtual environment ------------------------------------------
# Keeps ~2GB of ML dependencies out of the system Python and avoids the
# permission errors a global pip install hits on managed machines.
$venvDir = Join-Path $root ".venv"
if (-not (Test-Path $venvDir)) {
    Say ""
    Say "Creating virtual environment..." Cyan
    & $python -m venv $venvDir
}
$venvPy = Join-Path $venvDir "Scripts\python.exe"

Say ""
Say "Installing dependencies (~2GB, several minutes on first run)..." Cyan
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $app "requirements.txt")

# --- 3. Frontend -----------------------------------------------------
# The release ships a pre-built frontend, so Node is NOT required. Only
# build here if the build is missing and Node happens to be installed.
$dist = Join-Path $app "frontend\dist\index.html"
$fe = Join-Path $app "frontend"
if (Test-Path $dist) {
    Say "Frontend build found." Green
} elseif ((Test-Path $fe) -and (Get-Command node -ErrorAction SilentlyContinue)) {
    Say "Building frontend..." Cyan
    Push-Location $fe
    npm install --silent
    npm run build
    Pop-Location
} else {
    Say "No frontend build available." Yellow
    Say "The API will run, but the web page will not. Use the release" Yellow
    Say "package, which includes the built frontend." Yellow
}

# --- 4. Configuration ------------------------------------------------
$envFile = Join-Path $app ".env"
if (-not (Test-Path $envFile)) {
    Say ""
    Say "  Configuration" Cyan
    Say "  -------------" Cyan
    Say "GroundedOps can answer using a cloud AI provider (fast, needs an"
    Say "API key) or entirely offline on this PC (private, slower, needs"
    Say "Ollama and about 8GB of free memory)."
    Say ""
    $mode = Read-Host "Use a cloud provider? [Y/n]"

    $lines = @()
    $admin = Read-Host "Choose an admin password (for uploading documents)"
    if (-not $admin) { $admin = "admin" }
    $lines += "ADMIN_PASSWORD=$admin"

    if ($mode -ne "n") {
        Say ""
        Say "Which provider? 1) DeepSeek (cheapest)  2) OpenAI  3) Anthropic"
        $p = Read-Host "Enter 1, 2 or 3"
        switch ($p) {
            "2" { $prov = "openai";    $keyName = "OPENAI_API_KEY" }
            "3" { $prov = "anthropic"; $keyName = "ANTHROPIC_API_KEY" }
            default { $prov = "deepseek"; $keyName = "DEEPSEEK_API_KEY" }
        }
        $key = Read-Host "Paste your $prov API key"
        $lines += "GENERATION_MODE=api"
        $lines += "ONLINE_PROVIDER=$prov"
        $lines += "$keyName=$key"
    } else {
        $lines += "GENERATION_MODE=local"
        Say ""
        Say "Offline mode needs Ollama: https://ollama.com/download" Yellow
        Say "After installing it run:  ollama pull mistral" Yellow
    }

    $lines += "WIDGET_ALLOWED_ORIGINS=*"
    $lines += "ONLINE_DEEPSEEK_MODEL=deepseek-v4-flash"
    $lines | Set-Content -Path $envFile -Encoding UTF8
    Say ""
    Say "Wrote $envFile" Green
} else {
    Say "Existing .env kept - delete it and re-run to reconfigure." Green
}

# --- 5. Pre-download models ------------------------------------------
# Otherwise the tester's FIRST question stalls for minutes with no
# explanation while the models download in the background.
Say ""
Say "Downloading language models (~500MB, one time)..." Cyan
& $venvPy -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); print('models ready')"

Say ""
Say "  Install complete" Green
Say "  ================" Green
Say ""
Say "Start it:            double-click start.cmd"
Say "Share on your LAN:   double-click start-lan.cmd"
Say ""
Read-Host "Press Enter to close"
