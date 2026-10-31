# Undoes phone-access.ps1. This clears every `tailscale serve` setting on this machine.
$ErrorActionPreference = "Stop"
& tailscale serve reset
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Phone access is off."
