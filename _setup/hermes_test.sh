#!/usr/bin/env bash
export PATH="$HOME/.local/bin:$PATH"
echo "=================== hermes doctor ==================="
setsid -w hermes doctor < /dev/null 2>&1 | tail -40
echo "=================== one-shot prompt (-z) ==================="
setsid -w hermes -z "Reply with exactly the word: HERMES_OK" < /dev/null 2>&1 | tail -30
echo "=================== done ==================="
