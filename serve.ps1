<#
    serve.ps1 - keep GroundedOps running unattended.

    run.ps1 is the interactive launcher: it builds, checks the firewall,
    mints a test token, opens a test page, and holds the console. That is
    right for a person at the keyboard and wrong for a machine that has to
    serve a public website, because closing the window ends the site's chat
    and nothing brings it back.

    This is the unattended half. It does three things run.ps1 does not:

      * restarts the backend if it exits, with a backoff so a crash loop
        does not spin the CPU
      * prefers a NAMED tunnel, whose hostname is permanent, over a quick
        tunnel, whose hostname changes on every restart and therefore breaks
        the WordPress plugin's baked-in address
      * starts at boot, so a power cut does not need a human

    USAGE

      .\serve.ps1                  run in the foreground (Ctrl-C to stop)
      .\serve.ps1 -Install         start automatically at boot
      .\serve.ps1 -Uninstall       stop doing that
      .\serve.ps1 -Status          is it registered, is it up

    NAMED TUNNEL

    Set TUNNEL_HOSTNAME in src\.env once the tunnel exists, e.g.

        TUNNEL_HOSTNAME=chat.innovative-technology.com

    and this uses it. Without it, a quick tunnel is started instead and a
    warning is logged, because that address will not survive a restart.
    Creating the tunnel needs a Cloudflare login and a DNS record; both are
    one-time operator steps -- see TUNNEL_SETUP.md.
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Local,           # bind 127.0.0.1 only (no LAN)
    [switch]$NoTunnel,
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$TaskName = "GroundedOps Service"
$root = $PSScriptRoot
$app  = Join-Path $root "src"

# ---------------------------------------------------------------- install

if ($Install) {
    $ps = (Get-Command powershell).Source
    $a  = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Port $Port"
    if ($Local)    { $a += " -Local" }
    if ($NoTunnel) { $a += " -NoTunnel" }

    $action  = New-ScheduledTaskAction -Execute $ps -Argument $a -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    # RestartCount/Interval covers the case where this supervisor itself
    # dies; the loop below covers the backend dying, which is far likelier.
    # ExecutionTimeLimit 0 = never kill it for running too long.
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
              -DontStopIfGoingOnBatteries -StartWhenAvailable `
              -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

    # S4U runs at boot with no user logged on and WITHOUT storing a password.
    # If the account lacks the batch-logon right this throws, so fall back to
    # at-logon, which always works but needs someone to sign in after a
    # reboot -- said plainly rather than silently accepted.
    $me = "$env:USERDOMAIN\$env:USERNAME"
    try {
        $p = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $set -Principal $p -Force `
            -Description "Keeps the GroundedOps backend and tunnel running." | Out-Null
        Write-Host "Registered '$TaskName' - starts at boot, no sign-in needed."
    } catch {
        Write-Host "S4U registration failed ($($_.Exception.Message.Trim()))." -ForegroundColor Yellow
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
        $p = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $set -Principal $p -Force `
            -Description "Keeps the GroundedOps backend and tunnel running." | Out-Null
        Write-Host "Registered '$TaskName' - starts AT SIGN-IN." -ForegroundColor Yellow
        Write-Host "  After a reboot someone must sign in before the site's chat works."
    }
    Write-Host ""
    Write-Host "Sleep will still stop it. To keep the machine serving:"
    Write-Host "  powercfg /change standby-timeout-ac 0"
    Write-Host "  powercfg /change hibernate-timeout-ac 0"
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'"
    exit 0
}

if ($Status) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        $i = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "task      : $($t.State)  (trigger: $($t.Triggers[0].CimClass.CimClassName))"
        Write-Host "last run  : $($i.LastRunTime)  result $($i.LastTaskResult)"
    } else {
        Write-Host "task      : not registered"
    }
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 5
        Write-Host "backend   : up ($($r.StatusCode)) on port $Port"
    } catch {
        Write-Host "backend   : NOT responding on port $Port"
    }
    $env:GO_QUIET = "1"
    exit 0
}

# ------------------------------------------------------------------ serve

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$svcLog = Join-Path $logDir "service.log"

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $svcLog -Value $line -Encoding utf8
    Write-Host $line
}

# Read TUNNEL_HOSTNAME without importing the whole .env into this process:
# it holds API keys, and this script has no need of them.
$tunnelHost = $null
$envFile = Join-Path $app ".env"
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^\s*TUNNEL_HOSTNAME\s*=\s*(\S+)' |
         Select-Object -First 1
    if ($m) { $tunnelHost = $m.Matches[0].Groups[1].Value.Trim('"').Trim("'") }
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "no venv at $venvPy - run install.cmd first" }
$bind = if ($Local) { "127.0.0.1" } else { "0.0.0.0" }

# Another instance already listening is the likeliest operator error (a
# run.cmd window still open). Binding would just fail, so say why.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Log "port $Port is already in use by pid $($busy[0].OwningProcess) - is run.cmd still open? exiting."
    exit 1
}

$children = @()
function Stop-Children {
    foreach ($p in $children) {
        if ($p -and -not $p.HasExited) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

# ---- tunnel: named if we have a hostname, quick otherwise ----
if (-not $NoTunnel) {
    if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
        $tunLog = Join-Path $logDir "tunnel.log"
        if ($tunnelHost) {
            # 'tunnel run' reads ~/.cloudflared/<uuid>.json and the ingress
            # rules; the hostname is fixed by DNS, so the plugin's address
            # never changes again.
            $args = @("tunnel", "--no-autoupdate", "run", "--url",
                      "http://127.0.0.1:$Port", $tunnelHost)
            Log "starting NAMED tunnel -> https://$tunnelHost"
        } else {
            $args = @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port")
            Log "WARNING: no TUNNEL_HOSTNAME in src\.env - starting a QUICK tunnel."
            Log "         its address changes on every restart and will break the"
            Log "         WordPress plugin. See TUNNEL_SETUP.md."
        }
        $t = Start-Process -FilePath "cloudflared" -ArgumentList $args -PassThru `
                -NoNewWindow -RedirectStandardOutput $tunLog -RedirectStandardError "$tunLog.err"
        $children += $t
    } else {
        Log "cloudflared not installed - no public address. winget install --id Cloudflare.cloudflared"
    }
}

# ---- backend, supervised ----
$backoff = 5
$maxBackoff = 300
try {
    while ($true) {
        $log = Join-Path $logDir "backend.log"
        $be = Start-Process -FilePath $venvPy `
                -ArgumentList @("-m", "uvicorn", "main:app", "--host", $bind, "--port", "$Port") `
                -WorkingDirectory $app -PassThru -NoNewWindow `
                -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        $children += $be
        Log "backend started (pid $($be.Id)) on ${bind}:$Port"

        $started = Get-Date
        $be.WaitForExit()
        $ran = (Get-Date) - $started

        # A process that stayed up is a fresh incident, not a crash loop, so
        # its restart should be immediate; only repeated fast exits back off.
        if ($ran.TotalSeconds -gt 120) { $backoff = 5 }
        Log "backend exited (code $($be.ExitCode)) after $([int]$ran.TotalSeconds)s - restarting in ${backoff}s"
        Start-Sleep -Seconds $backoff
        $backoff = [Math]::Min($backoff * 2, $maxBackoff)
    }
} finally {
    Log "supervisor stopping - shutting down children"
    Stop-Children
}
