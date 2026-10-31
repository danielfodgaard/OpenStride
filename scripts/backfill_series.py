"""One-off: build series for activities that have a raw file but no series.

Run from the repo root: uv run python scripts/backfill_series.py
Set OPENSTRIDE_DATA_DIR to use another data folder. Activities imported before v3.1 have no
raw file; for those, import the file again and its series is added to the existing workout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import activities, storage  # noqa: E402


def backfill() -> list[str]:
    built = []
    for activity in storage.list_all_activities():
        raw = storage.find_raw(activity.id)
        if raw is None or (storage.has_series(activity.id) and activity.start_utc):
            continue
        activities.store_series(activity, activities.parse_bytes(raw.name, raw.read_bytes()))
        built.append(activity.id)
    return built


if __name__ == "__main__":
    print("Series built:", backfill() or "nothing to do")
