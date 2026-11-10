"""The mirror: what the active provider holds, written into the workspace's recorded tier.

Only this module writes activities/, wellness/ and athlete/profile.yaml, and it commits each write at
once. It also sends the plan out, to a provider that has a calendar, and notices when the calendar was
edited there.
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta

from app import clock, providers, workspace
from app.models import PlanDoc
from app.providers import ProviderError

FIRST_SYNC_DAYS = 180
OVERLAP_DAYS = 3  # a watch can upload a run days after it was recorded
INDEX_FIELDS = ("key", "id", "date", "start_local", "sport", "name", "distance_km", "moving_time_s", "avg_pace", "avg_hr", "load", "rpe", "planned_event_id", "partial")
PUSHED_FIELDS = ("date", "type", "modality", "title", "distance_km", "duration_min", "target_pace", "description", "steps")


class NotFound(Exception):
    pass


def folder():
    return workspace.root() / "activities"


def sync_state_path():
    return workspace.state() / "sync.json"


def sync_state() -> dict:
    path = sync_state_path()
    return json.loads(path.read_text()) if path.exists() else {}


def save_sync_state(state: dict) -> None:
    workspace.write_file(sync_state_path(), json.dumps(state, indent=1))


def rows() -> list[dict]:
    path = folder() / "index.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def find(reference: str) -> dict:
    """An index row by key or by the provider's id."""
    for row in rows():
        if reference in (row["key"], row["id"]):
            return row
    raise NotFound(f"There is no activity {reference!r}. `openstride activities` lists them.")


def load(reference: str) -> dict:
    return json.loads((folder() / f"{find(reference)['key']}.json").read_text())


def digest(value) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def pruned(value):
    """Without the fields that have no value: a provider's record is mostly nulls, and they cost the reader."""
    if isinstance(value, dict):
        return {k: pruned(v) for k, v in value.items() if v is not None and v != [] and v != {}}
    if isinstance(value, list):
        return [pruned(v) for v in value]
    return value


def new_key(record: dict, taken: set[str]) -> str:
    stamp = record["start_local"][:16].replace(":", "-")
    ident = re.sub(r"[^A-Za-z0-9]", "", record["id"])[-12:] or "x"
    key = f"{stamp}_{record['sport']}_{ident}"
    while key in taken:
        key += "x"
    return key


def write_wellness(days: list[dict]) -> int:
    by_month: dict[str, dict[str, dict]] = {}
    for day in days:
        by_month.setdefault(day["date"][:7], {})[day["date"]] = day
    for month, fresh in by_month.items():
        path = workspace.root() / "wellness" / f"{month}.jsonl"
        known = {}
        if path.exists():
            known = {json.loads(line)["date"]: json.loads(line) for line in path.read_text().splitlines() if line.strip()}
        known |= {day: record | {"source": pruned(record.get("source") or {})} for day, record in fresh.items()}
        workspace.write_file(path, "".join(json.dumps(known[day]) + "\n" for day in sorted(known)))
    return len(days)


def sync(since: date | None = None, today: date | None = None) -> dict:
    """Pull the provider's record into the workspace. Returns what is new, so a trigger can brief the agent."""
    provider = providers.require()
    today = today or clock.today()
    state = sync_state()
    if since is None:
        last = state.get("last_sync_date")
        since = date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS) if last else today - timedelta(days=FIRST_SYNC_DAYS)
    fetched = provider.activities(since, today)
    result = {"new": [], "updated": [], "removed": [], "wellness_days": 0, "notes": [], "since": since.isoformat()}
    with workspace.lock():
        known = {row["id"]: row for row in rows()}
        digests = state.get("digests", {})
        taken = {row["key"] for row in known.values()}
        seen = set()
        for record in fetched:
            seen.add(record["id"])
            mark = digest(record["source"] or record)
            existing = known.get(record["id"])
            if existing and digests.get(record["id"]) == mark:
                continue
            key = existing["key"] if existing else new_key(record, taken)
            taken.add(key)
            if not record["intervals"] and not record["partial"]:
                try:
                    record["intervals"] = provider.intervals(record["id"])
                except ProviderError as error:
                    result["notes"].append(f"No intervals for {record['name'] or key}: {error}")
                    mark = ""  # not remembered as done, so the next sync asks again
            record["intervals"] = [interval | {"source": pruned(interval.get("source") or {})} for interval in record["intervals"]]
            record["source"] = pruned(record["source"])
            workspace.write_file(folder() / f"{key}.json", json.dumps(record | {"key": key}, indent=1))
            (folder() / f"{key}.streams.json").unlink(missing_ok=True)  # the recording may have changed with it
            known[record["id"]] = {field: (record | {"key": key}).get(field) for field in INDEX_FIELDS}
            digests[record["id"]] = mark
            result["updated" if existing else "new"].append(key)
        for ident, row in list(known.items()):
            if ident not in seen and since.isoformat() <= row["date"] <= today.isoformat():
                (folder() / f"{row['key']}.json").unlink(missing_ok=True)
                (folder() / f"{row['key']}.streams.json").unlink(missing_ok=True)
                del known[ident]
                digests.pop(ident, None)
                result["removed"].append(row["key"])
        ordered = sorted(known.values(), key=lambda row: row["start_local"])
        workspace.write_file(folder() / "index.jsonl", "".join(json.dumps(row) + "\n" for row in ordered))

        try:
            result["wellness_days"] = write_wellness(provider.wellness(since, today))
        except ProviderError as error:
            result["notes"].append(f"Wellness was not synced: {error}")
        try:
            profile = provider.athlete()
            profile["source"] = pruned(profile.get("source") or {})
            if provider.capabilities.gear:
                profile["gear"] = provider.gear()
            workspace.write_file(workspace.root() / "athlete" / "profile.yaml", workspace.dump_yaml(profile))
        except ProviderError as error:
            result["notes"].append(f"The athlete profile was not synced: {error}")

        counts = ", ".join(f"{len(result[kind])} {kind}" for kind in ("new", "updated", "removed") if result[kind]) or "no new activities"
        workspace.commit_protected(f"Sync from {provider.name}: {counts}", ["activities", "wellness", "athlete/profile.yaml"])
        save_sync_state(state | {"last_sync_date": today.isoformat(), "last_sync_at": workspace.now_iso(), "digests": digests, "last_error": ""})
    return result


def streams(reference: str) -> dict:
    """One activity's recording, fetched on first need and kept beside the activity."""
    row = find(reference)
    path = folder() / f"{row['key']}.streams.json"
    if path.exists():
        return json.loads(path.read_text())
    data = providers.require().streams(row["id"])
    with workspace.lock():
        workspace.write_file(path, json.dumps(data))
    return data


def wellness(oldest: str = "", newest: str = "9999") -> list[dict]:
    days = []
    for path in sorted((workspace.root() / "wellness").glob("*.jsonl")):
        days += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [day for day in days if oldest <= day["date"] <= newest]


def profile() -> dict:
    return workspace.load_yaml(workspace.root() / "athlete" / "profile.yaml", {})


def calendar_path():
    return workspace.root() / "plan" / "calendar.json"


def calendar() -> dict:
    """session id -> {"event_id", "digest", "date", "title"}: what was sent to the provider's calendar."""
    path = calendar_path()
    return json.loads(path.read_text()) if path.exists() else {}


def event_pairs() -> dict[str, str]:
    """Provider event id -> session id, for the provider's own pairing of an activity with a planned event."""
    return {entry["event_id"]: session_id for session_id, entry in calendar().items()}


def push_calendar(doc: PlanDoc, today: date | None = None, force: bool = False) -> dict:
    """Send the sessions still ahead to the provider's calendar, and take back the ones that are gone or skipped."""
    provider = providers.require()
    result = {"sent": [], "removed": [], "notes": []}
    if not provider.capabilities.calendar:
        result["notes"].append(f"{provider.name} has no calendar; nothing was sent to a watch.")
        return result
    today = today or clock.today()
    context = {"cross_training": doc.request.cross_training}
    with workspace.lock():
        sent = calendar()
        ahead = {s.id: s for s in doc.plan.sessions() if s.date >= today and s.status == "planned"}
        for session_id, session in ahead.items():
            payload = session.model_dump(mode="json")
            mark = digest({field: payload[field] for field in PUSHED_FIELDS})
            entry = sent.get(session_id)
            if entry and entry["digest"] == mark and not force:
                continue
            try:
                made = provider.put_event(payload, context, entry["event_id"] if entry else None)
            except ProviderError as error:
                result["notes"].append(f"{session.title} on {session.date:%a %d %b} was not sent: {error}")
                continue
            sent[session_id] = {"event_id": made["id"], "digest": mark, "date": payload["date"], "title": session.title}
            result["sent"].append(session_id)
            result["notes"] += made["notes"]
        for session_id, entry in list(sent.items()):
            if session_id in ahead or entry["date"] < today.isoformat():
                continue  # what already happened stays on the calendar as it was
            try:
                provider.delete_event(entry["event_id"])
            except ProviderError as error:
                result["notes"].append(f"{entry['title']} could not be taken off the calendar: {error}")
                continue
            del sent[session_id]
            result["removed"].append(session_id)
        workspace.write_file(calendar_path(), json.dumps(sent, indent=1))
        workspace.commit_protected(
            f"Calendar on {provider.name}: {len(result['sent'])} sent, {len(result['removed'])} removed", ["plan/calendar.json"],
        )
    return result


def drift(today: date | None = None) -> list[str]:
    """Planned events the athlete changed on the provider since they were sent. Each is reported once."""
    provider = providers.active()
    sent = calendar()
    if provider is None or not provider.capabilities.calendar or not sent:
        return []
    today = today or clock.today()
    ahead = {sid: entry for sid, entry in sent.items() if entry["date"] >= today.isoformat()}
    if not ahead:
        return []
    last = max(entry["date"] for entry in ahead.values())
    events = {event["id"]: event for event in provider.events(today - timedelta(days=1), date.fromisoformat(last) + timedelta(days=60))}
    found = []
    for entry in ahead.values():
        event = events.get(entry["event_id"])
        when = f"{date.fromisoformat(entry['date']):%a %d %b}"
        if event is None:
            found.append(f"“{entry['title']}” on {when} was removed from the {provider.name} calendar.")
        elif event["date"] != entry["date"]:
            found.append(f"“{entry['title']}” was moved on the {provider.name} calendar from {when} to {date.fromisoformat(event['date']):%a %d %b}.")
        elif event["name"] != entry["title"]:
            found.append(f"“{entry['title']}” on {when} was renamed “{event['name']}” on the {provider.name} calendar.")
    state = sync_state()
    fresh = [item for item in found if item not in state.get("drift_reported", [])]
    save_sync_state(state | {"drift_reported": found})
    return fresh


def last_synced() -> datetime | None:
    stamp = sync_state().get("last_sync_at")
    return datetime.fromisoformat(stamp) if stamp else None
