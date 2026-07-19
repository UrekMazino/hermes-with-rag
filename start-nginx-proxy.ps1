# Project Y - start the nginx reverse proxy (:80 -> llama-server :8080)
# Runtime/prefix is kept OUT of the project at C:\Users\jcvia\nginx-proxy
$ErrorActionPreference = "Stop"
$nginx  = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "nginx.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
$prefix = "C:\Users\jcvia\nginx-proxy"

# Ensure runtime dirs exist (logs + temp leaves) so nginx can start
$dirs = @("$prefix\logs", "$prefix\temp\client_body_temp", "$prefix\temp\proxy_temp",
          "$prefix\temp\fastcgi_temp", "$prefix\temp\uwsgi_temp", "$prefix\temp\scgi_temp")
$dirs | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }

# Validate config first
& $nginx.FullName -p "$prefix" -c "conf\nginx.conf" -t
if ($LASTEXITCODE -ne 0) { throw "nginx config test failed" }

# nginx runs in the foreground on Windows, so launch it detached
Start-Process -FilePath $nginx.FullName -ArgumentList @('-p', $prefix, '-c', 'conf\nginx.conf') -WindowStyle Hidden
Start-Sleep -Milliseconds 1500
Write-Output "nginx started (prefix: $prefix), processes: $(@(Get-Process nginx -ErrorAction SilentlyContinue).Count)"
Write-Output "Stop with:  & '$($nginx.FullName)' -p '$prefix' -s stop"
