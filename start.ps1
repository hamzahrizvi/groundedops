# GroundedOps launcher - Windows
#
#   start.cmd        this PC only     (http://127.0.0.1:8000)
#   start-lan.cmd    share on the LAN (colleagues open your address)
#
# Keep this file pure ASCII. A curly quote or em dash in a UTF-8 file
# without a BOM is decoded as ANSI by Windows PowerShell 5.1, which turns
# byte 0x94 into a closing smart quote and silently ends a string.

param([switch]$Lan)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# The Python package may sit beside this script or in src\.
$app = $null
foreach ($candidate in @($root, (Join-Path $root "src"))) {
    if (Test-Path (Join-Path $candidate "main.py")) { $app = $candidate; break }
}
if (-not $app) {
    Write-Host "Could not find main.py here or in src\." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Not installed yet - run install.cmd first." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Warn early if the web UI has not been built - otherwise the browser just
# shows {"detail":"Not Found"} and it looks like the server is broken.
if (-not (Test-Path (Join-Path $app "frontend\dist\index.html"))) {
    Write-Host ""
    Write-Host "  WARNING: no frontend build found." -ForegroundColor Yellow
    Write-Host "  The API will run but the web page will show 'Not Found'." -ForegroundColor Yellow
    Write-Host "  Build it once with:" -ForegroundColor Yellow
    Write-Host "     cd $app\frontend ; npm install ; npm run build" -ForegroundColor Gray
    Write-Host ""
}

# 0.0.0.0 listens on every network interface; 127.0.0.1 accepts only this
# PC. Local-only is the default because there is no user authentication.
$bind = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }

function Get-LanIP {
    # Use the interface carrying the DEFAULT ROUTE. That is by definition
    # the adapter this PC uses to reach the rest of the network, so it is
    # the one colleagues can reach back on.
    #
    # Picking "the first private-range IPv4" instead returns things like
    # 172.31.80.1 - a WSL/Hyper-V virtual adapter. It looks like a valid LAN
    # address (172.16-31 is private) but exists only inside this PC, so
    # everyone else times out.
    try {
        $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop |
                 Sort-Object -Property RouteMetric |
                 Select-Object -First 1
        if ($route) {
            $addr = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction Stop |
                    Where-Object { $_.IPAddress -ne "127.0.0.1" } |
                    Select-Object -First 1
            if ($addr) { return $addr.IPAddress }
        }
    } catch {}

    try {
        $addr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object {
                    $_.IPAddress -ne "127.0.0.1" -and
                    $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL|Hyper|VirtualBox|VMware|Docker|Bluetooth|TAP|VPN"
                } |
                Sort-Object -Property InterfaceMetric |
                Select-Object -First 1
        if ($addr) { return $addr.IPAddress }
    } catch {}
    return $null
}

function Show-AllIPs {
    try {
        Write-Host "  All addresses on this PC (use the Wi-Fi or Ethernet one):" -ForegroundColor DarkGray
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -ne "127.0.0.1" } |
            ForEach-Object {
                Write-Host ("     {0,-16} {1}" -f $_.IPAddress, $_.InterfaceAlias) -ForegroundColor DarkGray
            }
    } catch {}
}

Write-Host ""
Write-Host "  Starting GroundedOps..." -ForegroundColor Cyan
Write-Host "  Loading models - the first start takes 30-60 seconds." -ForegroundColor DarkGray
Write-Host ""

if ($Lan) {
    if (-not (Get-NetFirewallRule -DisplayName "GroundedOps 8000" -ErrorAction SilentlyContinue)) {
        Write-Host "  Windows Firewall blocks incoming connections by default." -ForegroundColor Yellow
        Write-Host "  Approve the prompt to allow port 8000 (one time only)." -ForegroundColor Yellow
        # -Profile Any: a company LAN is usually classified Domain, so a
        # Private-only rule silently fails to apply on it.
        $fwCmd = "New-NetFirewallRule -DisplayName 'GroundedOps 8000' -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any"
        try {
            Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", $fwCmd
            Start-Sleep -Seconds 1
            if (Get-NetFirewallRule -DisplayName "GroundedOps 8000" -ErrorAction SilentlyContinue) {
                Write-Host "  Firewall rule added." -ForegroundColor Green
            } else {
                Write-Host "  Rule was not added - colleagues may not connect." -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  Declined. To add it later, run PowerShell as Administrator:" -ForegroundColor Yellow
            Write-Host "    $fwCmd" -ForegroundColor Gray
        }
    }

    $ip = Get-LanIP
    Write-Host ""
    Write-Host "  SHARE THIS ADDRESS:" -ForegroundColor Green
    if ($ip) {
        Write-Host "     http://${ip}:8000" -ForegroundColor Green
    } else {
        Write-Host "     Could not detect it automatically - pick one below." -ForegroundColor Yellow
    }
    Write-Host ""
    Show-AllIPs
    Write-Host ""
    Write-Host "  Colleagues need nothing installed - just that address." -ForegroundColor DarkGray
    Write-Host "  There is no login, and ingested documents can be downloaded" -ForegroundColor Yellow
    Write-Host "  by anyone who has it. Keep it to internal documents." -ForegroundColor Yellow
} else {
    Write-Host "  Open:  http://127.0.0.1:8000" -ForegroundColor Green
    Write-Host "  (use start-lan.cmd to share with your network)" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "  Stop: press Ctrl+C. This PC must stay awake while others use it." -ForegroundColor DarkGray
Write-Host ""

Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/settings" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { Start-Process "http://127.0.0.1:8000"; break }
        } catch {}
    }
} | Out-Null

# uvicorn must run from the application folder so "main:app" resolves and
# .env / relative data paths behave as they do under Docker.
Set-Location $app
& $venvPy -m uvicorn main:app --host $bind --port 8000
