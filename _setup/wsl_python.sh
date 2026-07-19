#!/usr/bin/env bash
set -e
if command -v uv >/dev/null 2>&1; then
  echo "uv already installed"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
echo "uv version:"
uv --version
echo "--- installing python 3.12 ---"
uv python install 3.12
echo "--- installed pythons ---"
uv python list --only-installed
