import os
from datetime import date, timedelta


def today() -> date:
    """The app's today. OPENSTRIDE_TODAY pins it so the fixed-date demo keeps working."""
    pinned = os.environ.get("OPENSTRIDE_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()


def week_starts(first_day: date, race_date: date) -> list[date]:
    """Mondays from the first on or after first_day through the race week."""
    start = first_day + timedelta(days=-first_day.weekday() % 7)
    return [start + timedelta(weeks=i) for i in range((race_date - start).days // 7 + 1)]
