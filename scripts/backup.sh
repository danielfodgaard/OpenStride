#!/usr/bin/env bash
# Writes openstride-backup-<date>.zip to a folder. Safe while the server runs.
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "Usage: scripts/backup.sh <folder> [--include-secrets]" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec uv run --quiet --project "$root" openstride backup "$@"
