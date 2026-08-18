# GroundedOps - one-command launcher. The only way to start this app -
# there is no separate start.cmd/start-lan.cmd, this replaces both:
#
#   run.cmd                 everything: build, LAN, tunnel, token, test page
#   run.cmd -Local           this PC only, no LAN binding (old start.cmd)
#   run.cmd -Local -NoTunnel -NoTestPage   plain local run, nothing extra
#   run.cmd -NoTunnel       LAN, but skip Cloudflare (old start-lan.cmd)
#   run.cmd -NoBuild        skip the frontend build even if it looks stale
#
# Does the five things that otherwise need five windows:
#   1. rebuilds the frontend IF the sources are newer than the build
#   2. ensures .env has a token secret and the sign-in URL
#   3. starts the backend on the LAN
#   4. opens a Cloudflare quick tunnel and captures the public URL
#   5. mints a member token and serves the test harness over HTTP
#
# Admin console (Nocturne, manage documents/FAQ/gaps/categories) is at
# /admin on the same backend - see the summary this script prints below.
#
# Keep this file pure ASCII - see the note in install.ps1.

param(
    [switch]$Local,
    [switch]$NoTunnel,
    [switch]$NoBuild,
    [switch]$NoTestPage
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$SIGN_IN_URL = "https://www.innovative-technology.com/my-account/"
$PORT = 8000
$TESTPORT = 5500

function Say($m, $c = "White") { Write-Host $m -ForegroundColor $c }
function Head($m) { Write-Host ""; Say "  $m" Cyan; Say ("  " + ("-" * $m.Length)) DarkGray }

$procs = @()   # everything we start, so Ctrl+C can clean up

# ---------------------------------------------------------------- app dir
$app = $null
foreach ($c in @($root, (Join-Path $root "src"))) {
    if (Test-Path (Join-Path $c "main.py")) { $app = $c; break }
}
if (-not $app) { Say "Could not find main.py here or in src\." Red; Read-Host "Enter to exit"; exit 1 }

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Say "Not installed - run install.cmd first." Red; Read-Host "Enter to exit"; exit 1 }

$envFile = Join-Path $app ".env"
$feDir   = Join-Path $app "frontend"
$distIdx = Join-Path $feDir "dist\index.html"

# ------------------------------------------------------------------- .env
Head "Configuration"

if (-not (Test-Path $envFile)) { New-Item -ItemType File -Path $envFile | Out-Null }
$envText = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
if ($null -eq $envText) { $envText = "" }

function Ensure-EnvLine($key, $value, $describe) {
    if ($envText -notmatch "(?m)^\s*$key\s*=") {
        Add-Content -Path $envFile -Value "$key=$value"
        $script:envText = Get-Content $envFile -Raw
        Say "  added $key  ($describe)" Green
        return $true
    }
    return $false
}

# A missing secret silently demotes every signed-in user to guest, so
# generate one rather than letting it fail quietly.
if ($envText -notmatch "(?m)^\s*WIDGET_TOKEN_SECRET\s*=\s*\S") {
    $secret = & $venvPy -c "import secrets;print(secrets.token_hex(32))"
    Ensure-EnvLine "WIDGET_TOKEN_SECRET" $secret "generated - use this same value in WordPress" | Out-Null
    Say "  NOTE: put this in wp-config.php as GROUNDEDOPS_SECRET" Yellow
} else {
    Say "  WIDGET_TOKEN_SECRET present" DarkGray
}

Ensure-EnvLine "WIDGET_SIGN_IN_URL" $SIGN_IN_URL "where guests are sent to sign in" | Out-Null
Ensure-EnvLine "ONLINE_DEEPSEEK_MODEL" "deepseek-v4-flash" "deepseek-chat was retired in July 2026" | Out-Null
Ensure-EnvLine "WIDGET_ALLOWED_ORIGINS" "*" "restrict to real domains before production" | Out-Null

# ------------------------------------------------------- frontend staleness
Head "Frontend"

function Build-Needed {
    if ($NoBuild) { return $false }
    if (-not (Test-Path $feDir)) { return $false }
    if (-not (Test-Path $distIdx)) { Say "  no build found" Yellow; return $true }
    $built = (Get-Item $distIdx).LastWriteTime
    # Compare against everything that ends up in the bundle. The widget
    # lives in public\, which Vite copies at BUILD time - so editing it
    # without rebuilding leaves the old file being served, which is exactly
    # the trap that made the widget keep calling the old endpoints.
    $newest = Get-ChildItem -Path (Join-Path $feDir "src"), (Join-Path $feDir "public") `
                -Recurse -File -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($newest -and $newest.LastWriteTime -gt $built) {
        Say ("  stale: " + $newest.Name + " is newer than the build") Yellow
        return $true
    }
    return $false
}

if (Build-Needed) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Say "  building..." Cyan
        Push-Location $feDir
        try {
            if (-not (Test-Path "node_modules")) { npm install --silent }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
            Say "  build complete" Green
        } finally { Pop-Location }
    } else {
        Say "  npm not found - serving the existing build (may be stale)" Yellow
    }
} else {
    Say "  build is up to date" Green
}

# Confirm the served widget is the current one, since a stale bundle looks
# identical to a broken backend from the browser.
$distWidget = Join-Path $feDir "dist\widget\groundedops-widget.js"
if (Test-Path $distWidget) {
    if (Select-String -Path $distWidget -Pattern "widget/catalog" -Quiet) {
        Say "  widget bundle: current (uses /widget/* endpoints)" Green
    } else {
        Say "  WARNING: bundled widget still calls the old endpoints." Yellow
        Say "  Copy the new groundedops-widget.js into frontend\public\widget\ and re-run." Yellow
    }
}

# ---------------------------------------------------------------- backend
Head "Backend"

$bind = if ($Local) { "127.0.0.1" } else { "0.0.0.0" }
$logFile = Join-Path $root "backend.log"
Remove-Item $logFile -ErrorAction SilentlyContinue

$backend = Start-Process -FilePath $venvPy `
    -ArgumentList @("-m", "uvicorn", "main:app", "--host", $bind, "--port", "$PORT") `
    -WorkingDirectory $app -PassThru -NoNewWindow `
    -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err"
$procs += $backend
Say "  starting (pid $($backend.Id)), loading models..." DarkGray

$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$PORT/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    if ($backend.HasExited) { break }
}
if (-not $ready) {
    Say "  backend did not come up - last lines of backend.log:" Red
    Get-Content $logFile -Tail 20 -ErrorAction SilentlyContinue
    Get-Content "$logFile.err" -Tail 20 -ErrorAction SilentlyContinue
    $procs | ForEach-Object { if (-not $_.HasExited) { Stop-Process -Id $_.Id -Force } }
    Read-Host "Enter to exit"; exit 1
}
Say "  ready" Green

# --------------------------------------------------------------- LAN address
function Get-LanIP {
    try {
        $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop |
                 Sort-Object RouteMetric | Select-Object -First 1
        if ($route) {
            $a = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction Stop |
                 Where-Object { $_.IPAddress -ne "127.0.0.1" } | Select-Object -First 1
            if ($a) { return $a.IPAddress }
        }
    } catch {}
    return $null
}
$lanIp = if ($Local) { $null } else { Get-LanIP }

if ($lanIp -and -not (Get-NetFirewallRule -DisplayName "GroundedOps 8000" -ErrorAction SilentlyContinue)) {
    Say "  adding firewall rule (approve the prompt)..." Yellow
    try {
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", `
            "New-NetFirewallRule -DisplayName 'GroundedOps 8000' -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any"
    } catch { Say "  declined - colleagues on the LAN may not connect" Yellow }
}

# ------------------------------------------------------------------ tunnel
$tunnelUrl = $null
if (-not $NoTunnel) {
    Head "Cloudflare tunnel"
    if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
        $tunLog = Join-Path $root "tunnel.log"
        Remove-Item $tunLog -ErrorAction SilentlyContinue
        $tun = Start-Process -FilePath "cloudflared" `
            -ArgumentList @("tunnel", "--url", "http://127.0.0.1:$PORT", "--no-autoupdate") `
            -PassThru -NoNewWindow -RedirectStandardOutput $tunLog -RedirectStandardError "$tunLog.err"
        $procs += $tun
        # cloudflared prints the hostname to stderr a second or two in.
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            foreach ($f in @("$tunLog.err", $tunLog)) {
                if (Test-Path $f) {
                    $m = Select-String -Path $f -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
                         Select-Object -First 1
                    if ($m) { $tunnelUrl = $m.Matches[0].Value; break }
                }
            }
            if ($tunnelUrl) { break }
        }
        if ($tunnelUrl) { Say "  $tunnelUrl" Green }
        else { Say "  tunnel started but no URL captured - check tunnel.log" Yellow }
    } else {
        Say "  cloudflared not installed - skipping" Yellow
        Say "  install with:  winget install --id Cloudflare.cloudflared" DarkGray
    }
}

# ------------------------------------------------------------------- token
Head "Test token"
$token = $null
try {
    $out = & $venvPy (Join-Path $app "mint_token.py") 2>&1
    $token = ($out | Where-Object { $_ -match "^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$" } | Select-Object -First 1)
    if ($token) { Say "  minted (valid 8h)" Green } else { Say "  could not mint - run mint_token.py manually" Yellow }
} catch { Say "  mint_token.py failed: $_" Yellow }

# ---------------------------------------------------------------- test page
$testUrl = $null
if (-not $NoTestPage) {
    $testDir = $null
    foreach ($d in @($root, $app, $feDir)) {
        if (Test-Path (Join-Path $d "widget-test.html")) { $testDir = $d; break }
    }
    if ($testDir) {
        # Served over HTTP, never file:// - a file:// page has a null origin
        # that no CORS policy can match, so every API call fails.
        $srv = Start-Process -FilePath $venvPy `
            -ArgumentList @("-m", "http.server", "$TESTPORT", "--bind", "127.0.0.1") `
            -WorkingDirectory $testDir -PassThru -NoNewWindow `
            -RedirectStandardOutput (Join-Path $root "testpage.log") `
            -RedirectStandardError (Join-Path $root "testpage.log.err")
        $procs += $srv
        $testUrl = "http://127.0.0.1:$TESTPORT/widget-test.html"
    }
}

# ----------------------------------------------------------------- summary
$summary = @()
$summary += ""
$summary += "  GroundedOps is running"
$summary += "  ======================"
$summary += ""
$summary += "  This PC          http://127.0.0.1:$PORT"
if ($lanIp)     { $summary += "  On the LAN       http://${lanIp}:$PORT" }
if ($tunnelUrl) { $summary += "  Public (tunnel)  $tunnelUrl" }
if ($testUrl)   { $summary += "  Test harness     $testUrl" }
$summary += "  Admin console    http://127.0.0.1:$PORT/admin"
$summary += ""
$summary += "  Sign-in URL      $SIGN_IN_URL"
if ($token) {
    $summary += ""
    $summary += "  Member token (paste into the test harness):"
    $summary += "  $token"
}
$summary += ""
if ($tunnelUrl) {
    $summary += "  Hand to the website team:"
    $summary += "    GROUNDEDOPS_API    $tunnelUrl"
    $summary += "    GROUNDEDOPS_SECRET (from .env - send separately, not by email)"
    $summary += ""
    $summary += "  NOTE: a quick tunnel URL changes every restart. Set up a named"
    $summary += "  tunnel on a real subdomain before anyone wires this into WordPress."
    $summary += ""
}

$summary | ForEach-Object {
    if ($_ -match "http") { Say $_ Green } elseif ($_ -match "NOTE|Hand to") { Say $_ Yellow } else { Say $_ }
}

$handover = Join-Path $root "handover.txt"
$summary | Set-Content -Path $handover -Encoding UTF8
Say "  (saved to handover.txt)" DarkGray
Say ""
Say "  Ctrl+C stops everything. Backend log follows:" DarkGray
Say ""

if ($testUrl) { Start-Process $testUrl }

# -------------------------------------------------------------- run / clean
try {
    Get-Content $logFile -Wait -Tail 5
} finally {
    Write-Host ""
    Say "Stopping..." Yellow
    foreach ($p in $procs) {
        try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } } catch {}
    }
    Say "Stopped." Green
}
