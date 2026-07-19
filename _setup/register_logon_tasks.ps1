# Registers Project Y llama-server + nginx as "at log on" scheduled tasks.
# Must run elevated (Task creation requires admin on this machine).
$ErrorActionPreference = "Continue"
$log = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\_setup\task_register.log"
"== run at $(Get-Date) (elevated: $([bool](([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)))) ==" | Out-File $log

$scripts = [ordered]@{
  "ProjectY-llama-server" = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-llama-server.ps1"
  "ProjectY-nginx-proxy"  = "C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-nginx-proxy.ps1"
}
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

foreach ($name in $scripts.Keys) {
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"{0}`"" -f $scripts[$name])
  try {
    Register-ScheduledTask -TaskName $name -Trigger $trigger -Principal $principal -Settings $settings -Action $action -Force -ErrorAction Stop | Out-Null
    "registered: $name" | Out-File $log -Append
  } catch { "FAILED ${name}: $($_.Exception.Message)" | Out-File $log -Append }
}
"--- verify ---" | Out-File $log -Append
Get-ScheduledTask -TaskName "ProjectY-*" | Select-Object TaskName, State | Format-Table -AutoSize | Out-String | Out-File $log -Append
"DONE" | Out-File $log -Append
