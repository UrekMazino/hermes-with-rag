#!/usr/bin/env bash
set -e
CFG="$HOME/.hermes/config.yaml"
cp "$CFG" "$CFG.bak"

# Point the model section at the local llama.cpp server (Windows host via WSL NAT gateway)
sed -i 's|^  default: "anthropic/claude-opus-4.6"|  default: "qwen3-30b"|' "$CFG"
sed -i 's|^  provider: "auto"|  provider: "custom"|' "$CFG"
sed -i 's|^  base_url: "https://openrouter.ai/api/v1"|  base_url: "http://172.17.144.1:8080/v1"|' "$CFG"
sed -i 's|^  # api_key: "your-key-here".*|  api_key: "sk-local"|' "$CFG"
sed -i 's|^  # context_length: 131072|  context_length: 16384|' "$CFG"

echo "=== effective model settings ==="
grep -nE '^  (default|provider|base_url|api_key|context_length):' "$CFG"
