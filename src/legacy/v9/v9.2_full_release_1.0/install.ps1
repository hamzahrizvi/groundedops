# GroundedOps installer — Windows (PowerShell)
# Usage:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Write-Host "=== GroundedOps installer ===" -ForegroundColor Cyan

# 1. Python
try { $py = (python --version) 2>&1; Write-Host "Found $py" }
catch { Write-Host "Python 3.11+ is required: https://www.python.org/downloads/" -ForegroundColor Red; exit 1 }

# 2. Node
try { $node = (node --version) 2>&1; Write-Host "Found Node $node" }
catch { Write-Host "Node.js 18+ is required: https://nodejs.org/" -ForegroundColor Red; exit 1 }

# 3. Python deps
Write-Host "`nInstalling Python dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 4. Frontend deps
Write-Host "`nInstalling frontend dependencies..." -ForegroundColor Cyan
Push-Location frontend
npm install
Pop-Location

# 5. Ollama (optional — Free/offline mode only)
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
  Write-Host "`nOllama found. Local models (mistral ~4GB, phi ~2GB) can be" -ForegroundColor Green
  Write-Host "downloaded now, or later from inside the app (Free mode dialog)."
  $pull = Read-Host "Download them now? [y/N]"
  if ($pull -eq "y") { ollama pull mistral; ollama pull phi }
} else {
  Write-Host "`nOllama NOT found — Online mode (API key) works without it." -ForegroundColor Yellow
  Write-Host "For Free/offline mode install Ollama: https://ollama.com/download"
}

Write-Host "`n=== Install complete ===" -ForegroundColor Green
Write-Host "Start the app:   .\start.ps1"
Write-Host "First launch opens in Online mode and asks for an API key"
Write-Host "(DeepSeek / OpenAI / Claude), or switch to Free mode in-app."
