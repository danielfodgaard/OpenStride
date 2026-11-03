#!/usr/bin/env bash
# On a server: fetch origin/main and rebuild the container if it moved. Meant for cron every few minutes.
# Wrapped in a function so bash has read the whole file before `git reset` replaces it underneath.
main() {
    set -euo pipefail
    cd "$(dirname "$0")/.."
    git fetch -q origin main
    [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
    echo "$(date -Is) deploying $(git rev-parse --short origin/main)"
    git reset -q --hard origin/main
    docker compose up -d --build --force-recreate
}
main "$@"
