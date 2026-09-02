<#
    backup_daily.ps1 - one unattended encrypted backup, for Task Scheduler.

    The backup itself already existed (manage_backup.py / backup.py, encrypted
    .gobk envelopes). What was missing was anything to RUN it on a timer:
    manage_backup.py documents BACKUP_PASSPHRASE as being "for unattended runs
    (cron)", but no such job was ever created. This is that job, plus the two
    things a scheduled backup needs and a manual one does not - rotation, so
    the disk does not fill silently, and a log, so a backup that has been
    failing for three weeks is visible before it matters.

    SETUP (once)

      1. Put the passphrase in src\.env, beside the other secrets:
             BACKUP_PASSPHRASE=<a long random passphrase>
         Store that passphrase somewhere else too. The archive is encrypted
         with it and there is no recovery path without it - not even with
         this tool.

      2. Register the task (adjust -At to taste):
             .\backup_daily.ps1 -Install -At 02:30

    Run it by hand any time to test:  .\backup_daily.ps1
#>
[CmdletBinding()]
param(
    # Where archives are written. Prefer a different physical disk - a backup
    # on the same drive as the original does not survive that drive failing.
    [string]$Dest = "$PSScriptRoot\backups",
    [int]$Keep = 14,
    [switch]$NoIndex,      # settings + documents only; smaller, slower restore
    [switch]$Install,
    [string]$At = "02:30",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "GroundedOps Daily Backup"

if ($Install) {
    $ps = (Get-Command powershell).Source
    $taskArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" " +
            "-Dest `"$Dest`" -Keep $Keep"
    if ($NoIndex) { $taskArgs += " -NoIndex" }
    $action  = New-ScheduledTaskAction -Execute $ps -Argument $taskArgs `
                                       -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    # RunOnlyIfNetworkAvailable is deliberately off: this writes to local
    # disk, and a laptop off the network still deserves its backup.
    $set = New-ScheduledTaskSettingsSet -StartWhenAvailable `
              -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
              -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName $TaskName -Action $action `
        -Trigger $trigger -Settings $set -Description `
        "Encrypted daily backup of GroundedOps documents, FAQ, accounts and index." `
        -Force | Out-Null
    Write-Host "Registered '$TaskName', daily at $At -> $Dest"
    Write-Host "Confirm BACKUP_PASSPHRASE is set in src\.env or the run will fail."
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'"
    exit 0
}

# ---- the actual backup -------------------------------------------------

$src    = Join-Path $PSScriptRoot "src"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$log = Join-Path $Dest "backup.log"
function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Host $line
}

# manage_backup.py reads src\.env itself, so the passphrase does not need to
# be in this process's environment - but fail early and clearly if it is
# absent, rather than letting the CLI stop to prompt a scheduler that has no
# console and hang until the 2-hour limit kills it.
$envFile = Join-Path $src ".env"
$hasPass = $env:BACKUP_PASSPHRASE -or
           ((Test-Path $envFile) -and
            (Select-String -Path $envFile -Pattern '^\s*BACKUP_PASSPHRASE\s*=\s*\S' -Quiet))
if (-not $hasPass) {
    Log "FAILED: BACKUP_PASSPHRASE is not set (src\.env or environment)."
    exit 1
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out   = Join-Path $Dest "groundedops-$stamp.gobk"
$argv  = @("manage_backup.py", "export", "--out", $out)
if ($NoIndex) { $argv += "--no-index" }

Log "starting export -> $(Split-Path $out -Leaf)"
Push-Location $src
try {
    $stdout = & $python @argv 2>&1
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($code -ne 0 -or -not (Test-Path $out)) {
    Log "FAILED (exit $code): $($stdout -join ' | ')"
    exit 1
}

$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Log "ok - $mb MB"

# Rotate only AFTER a verified good write, never before: a routine that
# deletes last week's copy and then fails has made things worse.
$old = Get-ChildItem $Dest -Filter "groundedops-*.gobk" |
       Sort-Object Name -Descending | Select-Object -Skip $Keep
foreach ($f in $old) {
    Remove-Item $f.FullName -Force
    Log "pruned $($f.Name)"
}
exit 0
