# QuantDex 每日定时任务安装/卸载脚本
# 安装：右键本文件 -> "使用 PowerShell 运行"
# 卸载：powershell -ExecutionPolicy Bypass -File scripts\13_setup_task.ps1 -Uninstall
param([switch]$Uninstall)

$TaskName = "QuantDex Daily"
$Bat = Join-Path $PSScriptRoot "run_daily.bat"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    schtasks /Delete /TN $TaskName /F 2>$null
    Write-Host "已删除任务: $TaskName"
    exit 0
}

if (-not (Test-Path $Bat)) {
    Write-Host "找不到 $Bat，请确认脚本目录完整"
    exit 1
}

try {
    $Action = New-ScheduledTaskAction -Execute $Bat
    $Trigger = New-ScheduledTaskTrigger -Daily -At 19:00
    # 关键：错过计划时补跑 + 电池模式也运行
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force -Description "QuantDex 每日行情更新 + 影子盘流水线 + 邮件" | Out-Null
    Write-Host "已创建任务: $TaskName（每天 19:00，错过计划会补跑）"
} catch {
    Write-Host "ScheduledTasks 模块不可用，回退到 schtasks：$($_.Exception.Message)"
    schtasks /Create /TN $TaskName /SC DAILY /ST 19:00 /TR "\"$Bat\"" /F
}

Write-Host ""
Write-Host "手动测试: schtasks /Run /TN \"$TaskName\""
Write-Host "查看状态: schtasks /Query /TN \"$TaskName\" /V /FO LIST"
Write-Host "运行日志: D:\MeineArbeit\QuantDex\data\store\logs\daily_run.log"
