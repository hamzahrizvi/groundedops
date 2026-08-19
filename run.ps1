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
$FW_NAME = "GroundedOps $PORT"
$FW_CMD  = "New-NetFirewallRule -DisplayName '$FW_NAME' -Direction Inbound " +
           "-Protocol TCP -LocalPort $PORT -Action Allow -Profile Any"

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
# Returns every address a colleague could actually reach, best first.
#
# The old version sorted the default routes by metric and took the first. On
# this machine Ethernet and Wi-Fi both hold a default route at metric 0, so the
# winner was a coin flip -- and it never checked whether the adapter was still
# connected. A disconnected Wi-Fi keeps both its route and its IP in the stack,
# so the launcher would cheerfully hand out an address that answers locally and
# is unreachable from anywhere else.
function Get-LanAddresses {
    # Virtual switches (WSL, Hyper-V) carry their own default routes and are
    # not reachable from the LAN either.
    $skip = 'Loopback|vEthernet|VMware|VirtualBox|Bluetooth|Local Area Connection\*'
    $out = @()
    try { $routes = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop }
    catch { return @() }

    foreach ($r in $routes) {
        $if = Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue
        if (-not $if -or $if.Status -ne 'Up') { continue }
        if ($if.InterfaceAlias -match $skip) { continue }

        $prof = Get-NetConnectionProfile -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue
        Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue |
          Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254.*" } |
          ForEach-Object {
            $out += [pscustomobject]@{
                IP     = $_.IPAddress
                Alias  = $if.InterfaceAlias
                Metric = $r.RouteMetric
                Online = if ($prof -and $prof.IPv4Connectivity -eq 'Internet') { 0 } else { 1 }
                Wired  = if ($if.InterfaceAlias -match 'Wi-?Fi|Wireless') { 1 } else { 0 }
            }
          }
    }
    $out | Sort-Object Online, Wired, Metric, IP
}

$lanAll = if ($Local) { @() } else { @(Get-LanAddresses) }
$lanIp  = if ($lanAll.Count) { $lanAll[0].IP } else { $null }

# Three states, not two. Get-NetFirewallRule returns an empty set both when
# no rule exists and when it cannot enumerate rules at all (no elevation, or
# domain policy) -- and inbound 8000 may well already be allowed by a policy
# or program rule under a different name. Treating "cannot tell" as "missing"
# means a red warning on every run of a setup that works, and a UAC prompt
# every run to re-add a rule that is already there.
#
# So: only act when the answer is known. Returns 'yes', 'no' or 'unknown'.
function Get-FwRuleState {
    try {
        $all = @(Get-NetFirewallRule -Direction Inbound -ErrorAction Stop)
        if (-not $all.Count) { return 'unknown' }    # enumeration gave us nothing
    } catch { return 'unknown' }

    $ours = @($all | Where-Object { $_.DisplayName -eq $FW_NAME })
    if ($ours | Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Allow' }) { return 'yes' }
    if ($ours.Count) { return 'blocked' }            # exists, but disabled or Block
    return 'no'
}

$fwState = if ($lanIp) { Get-FwRuleState } else { 'skip' }
if ($fwState -eq 'no' -or $fwState -eq 'blocked') {
    if ($fwState -eq 'blocked') { Say "  firewall rule exists but is disabled or blocking" Yellow }
    Say "  adding firewall rule (approve the prompt)..." Yellow
    try {
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", $FW_CMD
    } catch {}
    $fwState = Get-FwRuleState
}
switch ($fwState) {
    'yes'     { Say "  firewall rule in place" Green }
    'unknown' { Say "  could not read firewall rules - assuming port $PORT is open" DarkGray }
    'skip'    { }
    default   { Say "  no firewall rule for port $PORT (see summary)" Yellow }
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
if ($lanIp) {
    $summary += "  On the LAN       http://${lanIp}:$PORT   ($($lanAll[0].Alias))"
    foreach ($alt in ($lanAll | Select-Object -Skip 1)) {
        $summary += "    or             http://$($alt.IP):$PORT   ($($alt.Alias))"
    }
}
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
if ($lanIp -and $fwState -ne 'yes' -and $fwState -ne 'skip') {
    $summary += "  If a colleague cannot reach the LAN address, open the port once"
    $summary += "  in an ADMIN PowerShell:"
    $summary += ""
    $summary += "    $FW_CMD"
    $summary += ""
}
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
    if ($_ -match "WARNING") { Say $_ Red }
    elseif ($_ -match "http") { Say $_ Green }
    elseif ($_ -match "NOTE|Hand to|ADMIN") { Say $_ Yellow }
    else { Say $_ }
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
