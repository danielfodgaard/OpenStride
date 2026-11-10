#!/usr/bin/env bash
# Writes openstride-backup-<date>.zip to a folder. Safe while the server runs. Secrets are never included.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: scripts/backup.sh <folder>" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec uv run --quiet --project "$root" openstride backup "$@"
