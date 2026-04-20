# Register AI Daily tasks in Windows Task Scheduler.
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File E:\Develop\ai-daily\scheduler\setup_task.ps1

$ProjectRoot = "E:\Develop\ai-daily"

# ===== Daily task: 08:00 =====
$DailyName = "AIDaily-Pipeline"
$DailyAction = New-ScheduledTaskAction -Execute "$ProjectRoot\scheduler\run_daily.bat"
$DailyTrigger = New-ScheduledTaskTrigger -Daily -At "08:00"
$DailySettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $DailyName `
    -Action $DailyAction `
    -Trigger $DailyTrigger `
    -Settings $DailySettings `
    -Description "AI Daily pipeline (crawl + pick + summarize + render)" `
    -Force

Write-Host "[OK] Registered daily task: $DailyName  (every day 08:00)" -ForegroundColor Green

# ===== Bi-weekly reflection: every other Sunday 09:00 =====
$ReflectName = "AIDaily-Reflect"
$ReflectAction = New-ScheduledTaskAction -Execute "$ProjectRoot\scheduler\run_reflect.bat"
$ReflectTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "09:00" -WeeksInterval 2
$ReflectSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $ReflectName `
    -Action $ReflectAction `
    -Trigger $ReflectTrigger `
    -Settings $ReflectSettings `
    -Description "AI Daily bi-weekly weight reflection" `
    -Force

Write-Host "[OK] Registered reflection task: $ReflectName  (every 2nd Sunday 09:00)" -ForegroundColor Green

Write-Host ""
Write-Host "Commands you may find useful:"
Write-Host "  List tasks : Get-ScheduledTask -TaskName 'AIDaily-*'"
Write-Host "  Run now    : Start-ScheduledTask -TaskName 'AIDaily-Pipeline'"
$UninstallCmd = "Unregister-ScheduledTask -TaskName 'AIDaily-Pipeline','AIDaily-Reflect' -Confirm:" + '$false'
Write-Host "  Uninstall  : $UninstallCmd"
