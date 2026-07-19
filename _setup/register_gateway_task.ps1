# Registers the Hermes Discord gateway as an "at log on" scheduled task.
# Must run elevated (task creation requires admin on this machine).
$ErrorActionPreference = "Continue"
$log = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\_setup\gateway_task_register.log"
"== run at $(Get-Date) (elevated: $([bool](([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)))) ==" | Out-File $log

$name   = "ProjectY-hermes-gateway"
$script = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-hermes-gateway.ps1"
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
$action    = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"{0}`"" -f $script)

try {
  Register-ScheduledTask -TaskName $name -Trigger $trigger -Principal $principal -Settings $settings -Action $action -Force -ErrorAction Stop | Out-Null
  "registered: $name" | Out-File $log -Append
} catch { "FAILED ${name}: $($_.Exception.Message)" | Out-File $log -Append }

"--- verify ---" | Out-File $log -Append
& schtasks /query /tn $name /fo LIST 2>&1 | Select-String "TaskName|Status" | Out-File $log -Append
"DONE" | Out-File $log -Append
