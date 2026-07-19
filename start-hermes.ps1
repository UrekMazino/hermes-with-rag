# Project Y - Hermes launcher
# Puts Git Bash ahead of C:\Windows\System32\bash.exe (the WSL launcher) on PATH
# so the Hermes 'terminal' tool runs commands in Git Bash (which understands
# C:/ and /c/ paths) instead of shelling into WSL (where Windows paths fail).
# Forwards all args to hermes, e.g.:  .\start-hermes.ps1            (interactive)
#                                     .\start-hermes.ps1 -z "..."   (one-shot)
$env:PATH = "C:\Program Files\Git\bin;" + $env:PATH
# Ensure ripgrep (winget package dir is version-stamped) is reachable for search_files
$rg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\BurntSushi.ripgrep.MSVC_*\*\rg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($rg) { $env:PATH = $rg.DirectoryName + ";" + $env:PATH }
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe" @args
