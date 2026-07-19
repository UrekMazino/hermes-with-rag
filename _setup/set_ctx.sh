#!/usr/bin/env bash
CFG="$HOME/.hermes/config.yaml"
sed -i 's|^  context_length: 16384|  context_length: 65536|' "$CFG"
grep -nE '^  (provider|base_url|default|context_length):' "$CFG"
