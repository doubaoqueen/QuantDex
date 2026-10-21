# QuantDex daily scheduled task (install / uninstall)
# Install: right-click this file -> "Run with PowerShell"
# Uninstall: powershell -ExecutionPolicy Bypass -File scripts\13_setup_task.ps1 -Uninstall
param([switch]$Uninstall)

$TaskName = "QuantDex Daily"
$Bat = Join-Path $PSScriptRoot "run_daily.bat"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "Removed task: $TaskName"
    exit 0
}

if (-not (Test-Path $Bat)) {
    Write-Host "Missing file: $Bat"
    exit 1
}

try {
    $Action = New-ScheduledTaskAction -Execute $Bat
    $Trigger = New-ScheduledTaskTrigger -Daily -At 19:00
    # Catch up if the scheduled time was missed; run on battery too.
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force -Description "QuantDex daily pipeline" | Out-Null
    Write-Host "Created task: $TaskName (daily 19:00, catch-up enabled)"
} catch {
    Write-Host "ScheduledTasks module unavailable, fallback to schtasks: $($_.Exception.Message)"
    schtasks /Create /TN $TaskName /SC DAILY /ST 19:00 /TR $Bat /F
}

Write-Host ""
Write-Host ('Test run: schtasks /Run /TN "' + $TaskName + '"')
Write-Host ('Check:    schtasks /Query /TN "' + $TaskName + '" /V /FO LIST')
Write-Host ("Log:      D:\MeineArbeit\QuantDex\data\store\logs\daily_run.log")
