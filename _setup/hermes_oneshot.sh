#!/usr/bin/env bash
export PATH="$HOME/.local/bin:$PATH"
echo "=== one-shot prompt via local Qwen3 ==="
setsid -w hermes -z "Reply with exactly the word: HERMES_OK" < /dev/null 2>&1 | tail -25
echo "=== exit done ==="
