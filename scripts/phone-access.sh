#!/usr/bin/env bash
# Publishes the local server to your tailnet over HTTPS. The server stays bound to localhost.
set -euo pipefail

port="${OPENSTRIDE_PORT:-8000}"

fail() {
    echo "$1" >&2
    exit 1
}

command -v tailscale >/dev/null || fail "Tailscale is not installed. Get it from https://tailscale.com/download, log in, and run this again."

status=$(tailscale status --json 2>/dev/null) || fail "Tailscale is not running. Start it, run 'tailscale up', and run this again."
flat=$(tr -d ' \t\r\n' <<<"$status")
field() { grep -o "\"$1\":$2" <<<"$flat" | head -n 1 | cut -d '"' -f "$3" || true; }

state=$(field BackendState '"[^"]*"' 4)
[ "$state" = Running ] || fail "Tailscale is not logged in (state: ${state:-unknown}). Run 'tailscale up', and run this again."

# Without this `tailscale serve` would hang waiting on a browser step, so catch it first.
domain=$(field CertDomains '\["[^"]*"' 4)
[ -n "$domain" ] || fail "This tailnet has no HTTPS certificates yet. One-time setting: at https://login.tailscale.com/admin/dns enable MagicDNS and HTTPS Certificates, then run this again."

tailscale serve --bg --https=443 "http://127.0.0.1:$port"
echo
echo "OpenStride on your tailnet: https://$domain"
echo "Open it on your phone, then use Add to Home Screen. This computer must be on and running OpenStride."
