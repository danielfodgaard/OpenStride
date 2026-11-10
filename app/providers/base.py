"""The provider contract: what an analyzer must supply for OpenStride to coach on top of it.

A provider is the system of record for what the athlete did and measured. OpenStride mirrors it into
the workspace in a neutral shape: the fields every provider has, named once here, with the provider's
own record kept untouched under `source`. Nothing outside app/providers names a provider.

Required: activities, streams, wellness, athlete. Optional, and declared in `capabilities`:
calendar (planned sessions delivered to the watch), upload, wellness_write, manual, gear.
"""
from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol

SPORTS = ("run", "ride", "swim", "strength", "walk", "hike", "row", "ski", "other")

ACTIVITY_FIELDS = (
    "id", "provider", "date", "start_local", "sport", "name", "distance_km", "moving_time_s", "elapsed_time_s",
    "avg_pace", "avg_hr", "max_hr", "avg_cadence_spm", "elevation_gain_m", "load", "intensity", "rpe",
    "planned_event_id", "intervals", "partial", "partial_reason", "source",
)
WELLNESS_FIELDS = (
    "date", "provider", "fitness", "fatigue", "form", "ramp", "resting_hr", "hrv", "sleep_h", "sleep_score",
    "weight_kg", "soreness", "tiredness", "stress", "mood", "motivation", "readiness", "steps", "comments", "source",
)
PROFILE_FIELDS = (
    "provider", "name", "sex", "birth_date", "weight_kg", "hr_max", "hr_rest", "hr_threshold", "threshold_pace",
    "hr_zones", "pace_zones", "timezone", "source",
)
STREAM_COLUMNS = ("time_s", "distance_m", "hr", "speed_mps", "cadence", "altitude_m", "lat", "lon")


class ProviderError(Exception):
    """A failure with a message fit to show the athlete."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Unsupported(ProviderError):
    """The active provider doesn't have this capability."""


@dataclass(frozen=True)
class Capabilities:
    calendar: bool = False  # planned sessions become events the provider sends to the watch
    upload: bool = False  # a .fit or .gpx file can be handed to the provider
    wellness_write: bool = False  # what the athlete reports can be written back
    manual: bool = False  # a session with no recording can be logged
    gear: bool = False  # shoes and their mileage

    def as_dict(self) -> dict:
        return asdict(self)


class Provider(Protocol):
    name: str
    capabilities: Capabilities

    def check(self) -> dict:
        """Proves the credentials work. Returns {"athlete": name, ...}; raises ProviderError otherwise."""

    def activities(self, oldest: date, newest: date) -> list[dict]:
        """Every activity that started in the window, in the neutral shape, with its analysis."""

    def intervals(self, activity_id: str) -> list[dict]:
        """The laps or detected work and recovery blocks of one activity; asked for only when it is new or changed."""

    def streams(self, activity_id: str) -> dict:
        """{"activity_id", "provider", "columns": {name: [values]}}; STREAM_COLUMNS where recorded, the rest under their own names."""

    def wellness(self, oldest: date, newest: date) -> list[dict]:
        """One record per day that has any data."""

    def athlete(self) -> dict:
        """Zones, thresholds, max and resting heart rate."""

    # Optional capabilities. A provider without one raises Unsupported.
    def events(self, oldest: date, newest: date) -> list[dict]:
        """Planned events: {"id", "date", "name", "external_id"}."""

    def put_event(self, session: dict, context: dict, event_id: str | None = None) -> dict:
        """Create or replace the calendar event for a planned session. Returns {"id", "structured", "notes"}."""

    def delete_event(self, event_id: str) -> None: ...

    def upload(self, filename: str, content: bytes) -> list[str]:
        """Hand a recorded file to the provider. Returns the activity ids it became."""

    def put_wellness(self, day: date, fields: dict) -> None: ...

    def log_manual(self, entry: dict) -> str:
        """Log a session that has no recording. Returns its activity id."""

    def gear(self) -> list[dict]:
        """{"id", "name", "kind", "distance_km", "retired"}."""


def activity(**fields) -> dict:
    """A neutral activity with every field present, so a reader never needs to guess which are missing."""
    record = {
        "id": "", "provider": "", "date": "", "start_local": "", "sport": "other", "name": "", "distance_km": 0.0,
        "moving_time_s": 0, "elapsed_time_s": 0, "avg_pace": "", "avg_hr": 0, "max_hr": 0, "avg_cadence_spm": 0,
        "elevation_gain_m": 0.0, "load": None, "intensity": None, "rpe": None, "planned_event_id": "",
        "intervals": [], "partial": False, "partial_reason": "", "source": {},
    }
    unknown = set(fields) - set(record)
    if unknown:
        raise KeyError(f"not neutral activity fields: {sorted(unknown)}")
    return record | fields


def wellness_day(**fields) -> dict:
    record = dict.fromkeys(WELLNESS_FIELDS) | {"source": {}}
    unknown = set(fields) - set(record)
    if unknown:
        raise KeyError(f"not neutral wellness fields: {sorted(unknown)}")
    record |= fields
    if record["form"] is None and record["fitness"] is not None and record["fatigue"] is not None:
        record["form"] = round(record["fitness"] - record["fatigue"], 1)
    return record


def profile(**fields) -> dict:
    record = dict.fromkeys(PROFILE_FIELDS) | {"hr_zones": [], "pace_zones": [], "source": {}}
    unknown = set(fields) - set(record)
    if unknown:
        raise KeyError(f"not neutral profile fields: {sorted(unknown)}")
    return record | fields


def conformance(provider: Provider, oldest: date, newest: date) -> list[str]:
    """What a provider gets wrong against the contract; empty means it conforms. Every provider's tests run this."""
    problems = []
    if not isinstance(getattr(provider, "name", None), str) or not provider.name:
        problems.append("name must be a non-empty string")
    if not isinstance(getattr(provider, "capabilities", None), Capabilities):
        problems.append("capabilities must be a Capabilities")
    rows = provider.activities(oldest, newest)
    for row in rows:
        label = f"activity {row.get('id')!r}"
        missing = [f for f in ACTIVITY_FIELDS if f not in row]
        if missing:
            problems.append(f"{label} lacks {missing}")
            continue
        if not row["id"] or not isinstance(row["id"], str):
            problems.append(f"{label}: id must be a non-empty string")
        if row["provider"] != provider.name:
            problems.append(f"{label}: provider must be {provider.name!r}")
        if row["sport"] not in SPORTS:
            problems.append(f"{label}: sport {row['sport']!r} is not one of {SPORTS}")
        try:
            if date.fromisoformat(row["date"]).isoformat() != row["start_local"][:10]:
                problems.append(f"{label}: date must be the local start date")
        except (TypeError, ValueError):
            problems.append(f"{label}: date and start_local must be ISO strings")
        if not isinstance(row["source"], dict) or not isinstance(row["intervals"], list):
            problems.append(f"{label}: source must be a dict and intervals a list")
    if rows:
        if not isinstance(provider.intervals(rows[0]["id"]), list):
            problems.append("intervals must be a list")
        streams = provider.streams(rows[0]["id"])
        columns = streams.get("columns")
        if not isinstance(columns, dict):
            problems.append("streams must have a columns dict")
        elif len({len(values) for values in columns.values()}) > 1:
            problems.append("stream columns must all have the same length")
    for day in provider.wellness(oldest, newest):
        missing = [f for f in WELLNESS_FIELDS if f not in day]
        if missing:
            problems.append(f"wellness {day.get('date')!r} lacks {missing}")
    athlete = provider.athlete()
    missing = [f for f in PROFILE_FIELDS if f not in athlete]
    if missing:
        problems.append(f"athlete lacks {missing}")
    if provider.capabilities.calendar:
        for event in provider.events(oldest, newest):
            if not {"id", "date", "name", "external_id"} <= set(event):
                problems.append(f"event {event.get('id')!r} lacks id, date, name or external_id")
    return problems
