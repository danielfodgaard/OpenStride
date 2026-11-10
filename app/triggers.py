"""When the agent acts without being asked. Each trigger puts a short brief on the agent's queue, so it
is handled on the same session as the conversation and its result appears in the same stream.

The clock is checked once a minute. What has fired is remembered in the state folder, so a restart
doesn't repeat a brief.
"""
import asyncio
import json
import logging
import statistics
from datetime import date, datetime, timedelta

from app import clock, hub as hub_module, mirror, plan, providers, workspace
from app.providers import ProviderError

log = logging.getLogger(__name__)
TICK_S = 60
MIN_SYNC_MINUTES = 5
QUIET_EXEMPT = ("new_activity",)  # a run just done is worth hearing about; the rest waits for morning


def state() -> dict:
    path = workspace.state() / "triggers.json"
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        return {}


def save(data: dict) -> None:
    workspace.write_file(workspace.state() / "triggers.json", json.dumps(data, indent=1))


def remember(key: str, value) -> None:
    save(state() | {key: value})


def minutes(text: str) -> int:
    hours, _, mins = str(text).partition(":")
    return int(hours) * 60 + int(mins or 0)


def quiet(now: datetime, settings: dict) -> bool:
    try:
        start, end = minutes(settings["quiet_hours"]["from"]), minutes(settings["quiet_hours"]["to"])
    except (KeyError, ValueError):
        return False
    at = now.hour * 60 + now.minute
    return start <= at or at < end if start > end else start <= at < end


def unanswered(hub) -> bool:
    """The last thing in the conversation is something the coach said unprompted."""
    events = [e for e in hub.stream.history(limit=40) if e["type"] == "message"]
    return bool(events) and events[-1]["role"] == "coach" and events[-1].get("origin") not in ("athlete", "new_activity")


def activity_brief(keys: list[str]) -> str:
    rows = {row["key"]: row for row in mirror.rows()}
    lines = []
    for key in keys:
        row = rows.get(key)
        if row:
            amount = f"{row['distance_km']:g} km" if row.get("distance_km") else f"{round((row.get('moving_time_s') or 0) / 60)} min"
            lines.append(f"- activities/{key}.json: {row['sport']}, {row['date']}, {amount}, “{row.get('name') or ''}”")
    many = len(lines) > 1
    return (
        f"{'New activities were' if many else 'A new activity was'} synced from the provider:\n" + "\n".join(lines)
        + "\n\nFollow the review-run skill" + (" for each." if many else ".")
    )


def handle_sync(hub, result: dict, settings: dict, announce: bool = True) -> list[str]:
    """After a sync: brief the agent on activities it hasn't been told about. Returns the keys briefed."""
    data = state()
    known = set(data.get("briefed", []))
    fresh = [key for key in result.get("new", []) if key not in known]
    first_sync = "briefed" not in data
    data["briefed"] = sorted(known | set(result.get("new", [])))[-2000:]
    save(data)
    if first_sync or not fresh or not announce:
        return []  # the first sync brings months of history: nothing there is news
    recent_floor = (clock.today() - timedelta(days=4)).isoformat()
    rows = {row["key"]: row for row in mirror.rows()}
    fresh = [key for key in fresh if rows.get(key, {}).get("date", "") >= recent_floor]
    if fresh and settings["triggers"]["new_activity"]:
        hub.agent.enqueue("brief", activity_brief(fresh), "new_activity")
    return fresh


def sync_now(hub, announce: bool = True) -> dict:
    """One sync, with what follows from it. Blocking: call it from a thread."""
    settings = workspace.settings()
    try:
        result = mirror.sync()
    except ProviderError as error:
        data = state()
        if data.get("sync_error") != str(error):  # told once, not every fifteen minutes
            remember("sync_error", str(error))
            hub.stream.append("notice", level="warning", text=f"Sync failed: {error}")
        return {"error": str(error)}
    if state().get("sync_error"):
        remember("sync_error", "")
    result["briefed"] = handle_sync(hub, result, settings, announce)
    if settings["triggers"]["calendar_drift"] and plan.load() is not None:
        try:
            changes = mirror.drift()
        except ProviderError:
            changes = []
        if changes and announce:
            hub.agent.enqueue("brief", "The athlete changed planned sessions on the provider's calendar:\n" + "\n".join(f"- {c}" for c in changes)
                              + "\n\nAsk what they want: make the plan match (a proposal), or put the calendar back (`openstride source push --force`).", "calendar_drift")
    hub.stream.publish("data_changed")
    return result


def week_end_due(doc, today: date, data: dict) -> int | None:
    """The number of the last week whose planned days have all passed and that hasn't been reviewed."""
    done = set(data.get("week_end", []))
    finished = [w for w in doc.plan.weeks if w.start_date + timedelta(days=6) < today and w.sessions]
    if not finished:
        return None
    last = finished[-1]
    stale = today - (last.start_date + timedelta(days=6)) > timedelta(days=3)  # back from a long break: don't review old weeks
    return None if last.week_number in done or stale or doc.request.race_date < today else last.week_number


def wellness_signal(today: date) -> str | None:
    days = [d for d in mirror.wellness((today - timedelta(days=21)).isoformat(), today.isoformat())]
    latest = next((d for d in reversed(days) if d["date"] >= (today - timedelta(days=1)).isoformat()), None)
    if latest is None:
        return None
    earlier = [d for d in days if d["date"] < latest["date"]]
    found = []
    for field, label, worse in (("resting_hr", "Resting heart rate", lambda now, base: now >= base + 7),
                                ("hrv", "HRV", lambda now, base: now <= base * 0.8)):
        values = [d[field] for d in earlier if d.get(field)]
        if latest.get(field) and len(values) >= 7:
            base = statistics.median(values)
            if worse(latest[field], base):
                found.append(f"{label} is {latest[field]:g} against a three-week median of {base:g}")
    if latest.get("sleep_h") and latest["sleep_h"] < 5:
        found.append(f"sleep was {latest['sleep_h']:g} h")
    if latest.get("form") is not None and latest["form"] <= -30:
        found.append(f"form is {latest['form']:g}")
    return f"Wellness on {latest['date']}: " + "; ".join(found) + "." if found else None


def tick(hub, now: datetime | None = None) -> list[str]:
    """Fire what is due. Returns the triggers fired, for the log and the tests. Blocking."""
    now = now or clock.now()
    today = now.date()
    settings = workspace.settings()
    on = settings["triggers"]
    data = state()
    fired = []

    if hub_module.over_budget():
        if data.get("budget_notice") != f"{today:%Y-%m}":
            remember("budget_notice", f"{today:%Y-%m}")
            cap = settings["budget"]["monthly_usd"]
            hub.stream.append("notice", level="warning", text=f"This month's agent spend has reached the ${cap:g} cap in settings.yaml. The agent still answers you; its own check-ins are paused until next month or a higher cap.")
        return ["over_budget"]

    provider = providers.active()
    sync_every = max(int(settings["sync_minutes"] or 0), 0)
    last = mirror.last_synced()
    if provider is not None and sync_every and (last is None or (datetime.now(last.tzinfo) - last) >= timedelta(minutes=max(sync_every, MIN_SYNC_MINUTES))):
        result = sync_now(hub)
        if result.get("briefed"):
            fired.append("new_activity")
        data = state()

    night = quiet(now, settings)
    doc = plan.load()
    if doc is not None and not night:
        week = week_end_due(doc, today, data) if on["week_end"] else None
        if week is not None:
            remember("week_end", [*data.get("week_end", []), week])
            hub.agent.enqueue("brief", f"Week {week} of the plan is over. Follow the weekly-review skill.", "week_end")
            fired.append("week_end")
            data = state()
        if on["plan_end"] and doc.request.race_date < today and data.get("plan_end") != doc.request.race_date.isoformat():
            remember("plan_end", doc.request.race_date.isoformat())
            hub.agent.enqueue("brief", f"The race was on {doc.request.race_date:%A %d %B}. Follow the race-report skill.", "plan_end")
            fired.append("plan_end")
            data = state()
        current = doc.plan.week_of(today)
        if on["block_review"] and current and current.week_number > 1 and (current.week_number - 1) % 4 == 0 and today == current.start_date \
                and current.week_number not in data.get("block_review", []):
            remember("block_review", [*data.get("block_review", []), current.week_number])
            hub.agent.enqueue("brief", f"Weeks {current.week_number - 4} to {current.week_number - 1} are done. Follow the block-review skill.", "block_review")
            fired.append("block_review")
            data = state()

    if not night and not unanswered(hub):
        try:
            due = minutes(on["morning_brief_time"])
        except ValueError:
            due = minutes("07:30")
        at = now.hour * 60 + now.minute
        if on["morning_brief"] and due <= at < due + 180 and data.get("morning_brief") != today.isoformat():
            remember("morning_brief", today.isoformat())
            hub.agent.enqueue("brief", "Morning. Follow the daily-brief skill.", "morning_brief")
            fired.append("morning_brief")
            data = state()
        if on["wellness_signal"] and data.get("wellness_signal") != today.isoformat():
            signal = wellness_signal(today)
            if signal:
                remember("wellness_signal", today.isoformat())
                hub.agent.enqueue("brief", signal + " Ask how they feel before changing anything; one bad night is not a trend.", "wellness_signal")
                fired.append("wellness_signal")
    return fired


async def run(hub) -> None:
    while True:
        try:
            await asyncio.to_thread(tick, hub)
        except Exception:
            log.exception("A trigger check failed")
        await asyncio.sleep(TICK_S)
