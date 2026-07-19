# Project Y - Hermes messaging gateway (Discord, etc.)
# Same PATH fix as start-hermes.ps1 so the agent's terminal tool uses Git Bash
# (not WSL) and can find ripgrep when handling messages from Discord.
$env:PATH = "C:\Program Files\Git\bin;" + $env:PATH
$rg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\BurntSushi.ripgrep.MSVC_*\*\rg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($rg) { $env:PATH = $rg.DirectoryName + ";" + $env:PATH }
# Launch DETACHED in its own hidden console so a parent console close (Ctrl+Close /
# STATUS_CONTROL_C_EXIT) can't kill the gateway. Child inherits the PATH set above.
$hermes = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
Start-Process -FilePath $hermes -ArgumentList 'gateway','run' -WindowStyle Hidden
Write-Output "Hermes gateway launched (detached)."
