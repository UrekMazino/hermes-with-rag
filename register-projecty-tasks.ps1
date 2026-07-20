# Project Y - register logon Scheduled Tasks so the stack comes back by itself after a reboot.
#
# Without these, nothing auto-starts: after every restart the eLibrary shows
# "AI mode is temporarily unavailable" until you hand-start the RAG API, and queued catalog
# changes sit unindexed until you hand-run the worker. (See STARTING_HERMES_GUIDE.md.)
#
# Registers three tasks, staggered so each is up before the next needs it:
#   ProjectY-llama-server  (+15s)  the local Qwen model on 127.0.0.1:8080
#   ProjectY-rag-api       (+60s)  eLibrary AI-mode gateway on 127.0.0.1:8090 (needs llama-server)
#   ProjectY-sync-worker   (+90s)  drains rag_sync_queue -> LanceDB every 30s
#
# The Discord bot/gateway is registered separately by _setup\register_gateway_task.ps1.
#
# RUN ONCE (elevate only if a normal run reports Access Denied):
#   powershell -ExecutionPolicy Bypass -File C:\Users\jcvia\PyCharmMiscProject\ProjectY\register-projecty-tasks.ps1

$root = "C:\Users\jcvia\PyCharmMiscProject\ProjectY"

function Register-ProjectYTask {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Script,
        [int]$DelaySeconds = 0,
        [string]$Description = ""
    )

    if (-not (Test-Path $Script)) {
        Write-Warning ("  SKIPPED " + $Name + " - launcher not found: " + $Script)
        return
    }

    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""

    $trigger = New-ScheduledTaskTrigger -AtLogOn
    if ($DelaySeconds -gt 0) { $trigger.Delay = "PT${DelaySeconds}S" }

    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel Limited

    # Long-running services: no execution time limit, restart a few times if they die.
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)

    try {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Description $Description -Force `
            -ErrorAction Stop | Out-Null
        Write-Host ("  registered " + $Name + "  (logon +" + $DelaySeconds + "s)")
    }
    catch {
        $script:AnyFailed = $true
        Write-Warning ("  FAILED " + $Name + " - " + $_.Exception.Message)
    }
}

$script:AnyFailed = $false
Write-Host "Registering Project Y logon tasks..."

Register-ProjectYTask -Name "ProjectY-llama-server" -Script "$root\start-llama-server.ps1" `
    -DelaySeconds 15 -Description "Local Qwen model (llama.cpp) on 127.0.0.1:8080. Needed by AI mode and the Discord bot."

Register-ProjectYTask -Name "ProjectY-rag-api" -Script "$root\start-rag-api.ps1" `
    -DelaySeconds 60 -Description "eLibrary AI-mode gateway on 127.0.0.1:8090. Needs llama-server."

Register-ProjectYTask -Name "ProjectY-sync-worker" -Script "$root\start-sync-worker.ps1" `
    -DelaySeconds 90 -Description "Drains rag_sync_queue into LanceDB so the AI index stays current."

Write-Host ""
if ($script:AnyFailed) {
    Write-Warning "One or more tasks were NOT registered (usually 'Access is denied')."
    Write-Host    "Re-run this in an ELEVATED PowerShell (Run as Administrator):" -ForegroundColor Yellow
    Write-Host    "  powershell -ExecutionPolicy Bypass -File `"$root\register-projecty-tasks.ps1`"" -ForegroundColor Yellow
    Write-Host    ""
}

# Show what actually exists now, so the output can't claim success it didn't achieve.
$existing = Get-ScheduledTask -TaskName "ProjectY-*" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Currently registered:"
    $existing | ForEach-Object { Write-Host ("  " + $_.TaskName + "  [" + $_.State + "]") }
} else {
    Write-Host "Currently registered: (none)"
}

Write-Host ""
Write-Host "Once registered they start automatically at next logon. Useful commands:"
Write-Host '  Start now:   schtasks /run /tn ProjectY-llama-server   (then -rag-api, -sync-worker)'
Write-Host '  Status:      Get-ScheduledTask -TaskName ProjectY-* | Select TaskName,State'
Write-Host '  Stop one:    schtasks /end /tn ProjectY-rag-api'
Write-Host '  Remove one:  Unregister-ScheduledTask -TaskName ProjectY-rag-api -Confirm:$false'
Write-Host ""
Write-Host "NOTE: start these only ONE way. If a service is already running by hand, the task's copy"
Write-Host "      will fail to bind its port (8080 / 8090). At logon nothing is running, so that is"
Write-Host "      only an issue if you also launch them manually in the same session."
