#!/usr/bin/env bash
# Undoes phone-access.sh. This clears every `tailscale serve` setting on this machine.
set -euo pipefail
tailscale serve reset
echo "Phone access is off."
