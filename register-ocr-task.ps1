# Project Y - register a Scheduled Task that runs the Stage 3 OCR batch DURABLY.
# The task runs start-ocr-batch.ps1 at every logon, so the resumable OCR survives session ends,
# logoffs, and reboots (auto-resuming each time) until the corpus is fully OCR'd. The launcher is
# self-guarding: once nothing is pending it exits without touching llama-server.
#
# RUN THIS ELEVATED (Admin PowerShell) ONCE if a normal run reports Access Denied:
#   powershell -ExecutionPolicy Bypass -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\register-ocr-task.ps1

$Task     = "ProjectY-ocr-batch"
$launcher = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-ocr-batch.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ("Registered '" + $Task + "' - runs the OCR batch at every logon until done.")
Write-Host '  Start now:        schtasks /run /tn ProjectY-ocr-batch'
Write-Host '  Check progress:   cd C:\Users\jcvia\PyCharmMiscProject\ProjectY\rag; .\.venv\Scripts\python.exe .\ocr_run.py --status'
Write-Host '  Stop a run:       schtasks /end /tn ProjectY-ocr-batch'
Write-Host '  Remove when done: Unregister-ScheduledTask -TaskName ProjectY-ocr-batch -Confirm:$false'
