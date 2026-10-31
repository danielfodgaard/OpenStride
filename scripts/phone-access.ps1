# Publishes the local server to your tailnet over HTTPS. The server stays bound to localhost.
$ErrorActionPreference = "Stop"

$port = if ($env:OPENSTRIDE_PORT) { $env:OPENSTRIDE_PORT } else { "8000" }

function Fail($message) {
    Write-Error $message -ErrorAction Continue
    exit 1
}

if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    Fail "Tailscale is not installed. Get it from https://tailscale.com/download, log in, and run this again."
}

$raw = & tailscale status --json 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail "Tailscale is not running. Start it, run 'tailscale up', and run this again."
}
$status = ($raw -join "`n") | ConvertFrom-Json

if ($status.BackendState -ne "Running") {
    Fail "Tailscale is not logged in (state: $($status.BackendState)). Run 'tailscale up', and run this again."
}

# Without this `tailscale serve` would hang waiting on a browser step, so catch it first.
$domain = @($status.CertDomains)[0]
if (-not $domain) {
    Fail "This tailnet has no HTTPS certificates yet. One-time setting: at https://login.tailscale.com/admin/dns enable MagicDNS and HTTPS Certificates, then run this again."
}

& tailscale serve --bg --https=443 "http://127.0.0.1:$port"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output ""
Write-Output "OpenStride on your tailnet: https://$domain"
Write-Output "Open it on your phone, then use Add to Home Screen. This computer must be on and running OpenStride."
