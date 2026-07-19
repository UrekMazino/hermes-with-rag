#!/usr/bin/env bash
export PATH="$HOME/.local/bin:$PATH"
echo "=== hermes on PATH? ==="
command -v hermes || echo "hermes NOT on PATH"
echo "=== ~/.hermes top level ==="
ls -la "$HOME/.hermes" 2>/dev/null
echo "=== config.yaml (if present) ==="
cat "$HOME/.hermes/config.yaml" 2>/dev/null || echo "no config.yaml"
echo "=== .env (keys only) ==="
sed -n '1,40p' "$HOME/.hermes/.env" 2>/dev/null || echo "no .env"
