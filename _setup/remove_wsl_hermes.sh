#!/usr/bin/env bash
echo "=== before: hermes shims in ~/.local/bin ==="
ls -la "$HOME/.local/bin" 2>/dev/null | grep -i hermes || echo "(none)"
echo "=== removing ~/.hermes (size) ==="
du -sh "$HOME/.hermes" 2>/dev/null || echo "(no ~/.hermes)"
rm -rf "$HOME/.hermes"
echo "=== removing hermes command shims ==="
rm -f "$HOME/.local/bin/hermes" "$HOME/.local/bin/hermes-acp" "$HOME/.local/bin/hermes-agent"
echo "=== after ==="
ls -la "$HOME/.local/bin" 2>/dev/null | grep -i hermes && echo "STILL PRESENT" || echo "hermes shims gone"
[ -d "$HOME/.hermes" ] && echo "~/.hermes STILL PRESENT" || echo "~/.hermes removed"
echo "=== uv/python preserved? ==="
command -v uv && uv --version
