#!/usr/bin/env bash
# Ensure uv (installed to ~/.local/bin) is on PATH for the installer
export PATH="$HOME/.local/bin:$PATH"
echo "PATH check - uv at: $(command -v uv || echo MISSING)"
echo "=== Running Hermes installer (--skip-setup) ==="
setsid -w bash /mnt/c/Users/jcvia/PyCharmMiscProject/ProjectY/_setup/hermes_install.sh --skip-setup --non-interactive < /dev/null
echo "=== INSTALLER EXIT: $? ==="
echo "=== ~/.hermes contents ==="
ls -la "$HOME/.hermes" 2>/dev/null || echo "no ~/.hermes"
