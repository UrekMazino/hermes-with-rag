#!/usr/bin/env bash
# Windows host IP as seen from WSL2 (NAT mode = default gateway)
ip route show default | awk '{print $3}'
