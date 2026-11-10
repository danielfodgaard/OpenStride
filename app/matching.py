"""Which activity counts as which planned session. Computed every time, never stored in the record.

Order of authority: the athlete's or agent's correction in notes/<activity>/match.json, then the
provider's own pairing with a calendar event, then same day and same kind.
"""
import json
from dataclasses import dataclass, field

from app import workspace
from app.models import DatedPlan

MODALITY = {"run": "run", "strength": "strength"}  # every other sport is cross-training


def modality(sport: str) -> str:
    return MODALITY.get(sport, "cross")


@dataclass
class Matching:
    by_session: dict[str, list[str]] = field(default_factory=dict)  # session id -> activity keys
    by_activity: dict[str, str] = field(default_factory=dict)  # activity key -> session id, "" when unplanned

    def done(self) -> set[str]:
        return set(self.by_session)


def overrides() -> dict[str, str]:
    """activity key -> session id ("" for "this was not a planned session")."""
    found = {}
    for path in (workspace.root() / "notes").glob("*/match.json"):
        try:
            found[path.parent.name] = str(json.loads(path.read_text()).get("session_id") or "")
        except (OSError, ValueError, AttributeError):
            continue
    return found


def compute(plan: DatedPlan | None, activities: list[dict], corrections: dict[str, str] | None = None, pairs: dict[str, str] | None = None) -> Matching:
    """`activities` are index rows; `pairs` maps a provider event id to a session id."""
    result = Matching()
    if plan is None:
        return Matching(by_activity={a["key"]: "" for a in activities})
    corrections = corrections or {}
    pairs = pairs or {}
    sessions = {s.id: s for s in plan.sessions()}

    def assign(key: str, session_id: str) -> None:
        result.by_activity[key] = session_id
        if session_id:
            result.by_session.setdefault(session_id, []).append(key)

    rows = sorted(activities, key=lambda a: a.get("start_local") or a.get("date") or "")
    waiting = []
    for row in rows:
        key = row["key"]
        if key in corrections:
            assign(key, corrections[key] if corrections[key] in sessions else "")
        elif pairs.get(str(row.get("planned_event_id") or "")) in sessions:
            assign(key, pairs[str(row["planned_event_id"])])
        else:
            waiting.append(row)
    for row in waiting:
        kind = modality(row.get("sport", ""))
        candidates = [
            s for s in plan.sessions()
            if s.date.isoformat() == row.get("date") and s.modality == kind and s.status != "skipped" and s.id not in result.by_session
        ]
        assign(row["key"], candidates[0].id if candidates else "")
    return result
