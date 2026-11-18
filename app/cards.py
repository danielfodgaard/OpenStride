"""Cards: what the athlete sees of their data. An agent asks for one by type; code builds it from the
workspace, the same way every time, and the page draws it. A card is built again whenever it is shown,
so a week card always shows the plan as it stands.
"""
import json
from datetime import date, datetime, timedelta

from app import clock, matching, mirror, plan, providers, workspace
from app.hub import hub
from app.models import PlanDoc

HARD_TYPES = ("tempo", "intervals", "long", "race")


class CardError(Exception):
    """A card that can't be shown, with a message the agent can act on."""


def matches(doc: PlanDoc | None) -> matching.Matching:
    return matching.compute(doc.plan if doc else None, mirror.rows(), matching.overrides(), mirror.event_pairs())


def activity_brief(row: dict) -> dict:
    return {key: row.get(key) for key in ("key", "date", "sport", "name", "distance_km", "moving_time_s", "avg_pace", "avg_hr", "load", "rpe", "partial")}


def session_state(session, today: date, matched: bool) -> str:
    if session.status != "planned":
        return session.status
    if matched:
        return "done"
    if session.date < today:
        return "missed"
    return "today" if session.date == today else "upcoming"


def session_view(session, today: date, found: matching.Matching, by_key: dict) -> dict:
    keys = found.by_session.get(session.id, [])
    return session.model_dump(mode="json") | {
        "state": session_state(session, today, bool(keys)),
        "activities": [activity_brief(by_key[key]) for key in keys if key in by_key],
    }


def week_view(doc: PlanDoc, week, today: date, found: matching.Matching, by_key: dict) -> dict:
    sessions = [session_view(s, today, found, by_key) for s in sorted(week.sessions, key=lambda s: (s.date, s.modality != "run"))]
    end = week.start_date + timedelta(days=6)
    extra = [
        activity_brief(row) for row in by_key.values()
        if week.start_date.isoformat() <= row["date"] <= end.isoformat() and not found.by_activity.get(row["key"])
    ]
    in_week = [row for row in by_key.values() if week.start_date.isoformat() <= row["date"] <= end.isoformat()]
    countable = [s for s in sessions if s["state"] not in ("skipped",)]
    return {
        "week_number": week.week_number, "of": len(doc.plan.weeks), "phase": week.phase, "focus": week.focus,
        "start_date": week.start_date.isoformat(), "end_date": end.isoformat(),
        "planned_km": week.planned_km,
        "done_km": round(sum(row["distance_km"] or 0 for row in in_week if row["sport"] == "run"), 1),
        "sessions_planned": len(countable),
        "sessions_done": sum(s["state"] in ("done", "replaced") for s in countable),
        "current": week.start_date <= today <= end, "future": week.start_date > today,
        "sessions": sessions, "unplanned": extra,
    }


def pick_week(doc: PlanDoc, which, today: date):
    if which in (None, "", "this", "current"):
        return doc.plan.week_of(today) or (doc.plan.weeks[0] if today < doc.plan.weeks[0].start_date else doc.plan.weeks[-1])
    if which == "next":
        return doc.plan.week_of(today + timedelta(days=7)) or doc.plan.weeks[-1]
    if which in ("last", "previous"):
        return doc.plan.week_of(today - timedelta(days=7)) or doc.plan.weeks[0]
    try:
        week = doc.plan.week(int(which))
    except (TypeError, ValueError):
        week = None
    if week is None:
        raise CardError(f"The plan has weeks 1 to {len(doc.plan.weeks)}; {which!r} is not one. Use a number, this, next or last.")
    return week


def need_plan() -> PlanDoc:
    doc = plan.load()
    if doc is None:
        raise CardError("There is no plan yet, so there is nothing to show. Make one with the new-plan skill.")
    return doc


def week_card(week=None, **_) -> dict:
    doc, today = need_plan(), clock.today()
    return week_view(doc, pick_week(doc, week, today), today, matches(doc), {row["key"]: row for row in mirror.rows()})


def status(week=None) -> dict:
    """The computed view an agent reads instead of working it out: one week against what was done."""
    return week_card(week) | {"today": clock.today().isoformat()}


def session_card(id: str = "", **_) -> dict:
    doc, today = need_plan(), clock.today()
    session = doc.plan.session(id)
    if session is None:
        raise CardError(f"The plan has no session {id!r}. Session ids are in plan/plan.json.")
    week = doc.plan.week_of(session.date)
    return session_view(session, today, matches(doc), {row["key"]: row for row in mirror.rows()}) | {"week_number": week.week_number if week else None}


def provider_link(kind: str, ident: str = "") -> dict | None:
    """Where the provider shows this itself, with its own charts. OpenStride draws none."""
    provider = providers.active()
    url = provider.web_url(kind, ident) if provider is not None and hasattr(provider, "web_url") else ""
    return {"url": url, "label": f"Open in {provider.name}"} if url else None


def note_file(*parts: str) -> str:
    path = workspace.root().joinpath(*parts)
    return path.read_text() if path.exists() else ""


def note_text(key: str, name: str) -> str:
    return note_file("notes", key, name)


def activity_card(key: str = "", id: str = "", **_) -> dict:
    try:
        record = mirror.load(key or id)
    except mirror.NotFound as error:
        raise CardError(str(error)) from error
    doc = plan.load()
    found = matches(doc)
    session_id = found.by_activity.get(record["key"], "")
    session = doc.plan.session(session_id) if doc and session_id else None
    return {k: v for k, v in record.items() if k != "source"} | {
        "planned": session.model_dump(mode="json") if session else None,
        "review": note_text(record["key"], "review.md"),
        "athlete_note": note_text(record["key"], "athlete.md"),
        "link": provider_link("activity", record.get("id") or ""),
    }


def activities_card(limit: int = 10, **_) -> dict:
    rows = mirror.rows()[-int(limit):][::-1]
    found = matches(plan.load())
    return {"activities": [activity_brief(row) | {"session_id": found.by_activity.get(row["key"], "")} for row in rows]}


def phase_spans(doc: PlanDoc, today: date) -> list[dict]:
    spans = []
    for week in doc.plan.weeks:
        end = week.start_date + timedelta(days=6)
        if spans and spans[-1]["phase"] == week.phase:
            spans[-1] |= {"to_week": week.week_number, "end_date": end.isoformat()}
        else:
            spans.append({"phase": week.phase, "from_week": week.week_number, "to_week": week.week_number, "start_date": week.start_date.isoformat(), "end_date": end.isoformat()})
    for span in spans:
        span["current"] = span["start_date"] <= today.isoformat() <= span["end_date"]
        span["past"] = span["end_date"] < today.isoformat()
        span["planned_km"] = round(sum(w.planned_km for w in doc.plan.weeks if span["from_week"] <= w.week_number <= span["to_week"]), 1)
    return spans


def plan_card(**_) -> dict:
    doc, today = need_plan(), clock.today()
    found, by_key = matches(doc), {row["key"]: row for row in mirror.rows()}
    current = pick_week(doc, "this", today)
    return {
        "goal": doc.request.goal_description, "race_date": doc.request.race_date.isoformat(), "race_distance_km": doc.request.race_distance_km,
        "goal_time": doc.request.goal_time, "days_to_go": (doc.request.race_date - today).days, "summary": doc.plan.summary,
        "warnings": doc.plan.warnings, "paces": doc.paces.model_dump(), "phases": phase_spans(doc, today), "weeks": len(doc.plan.weeks),
        "current_week": current.week_number if current.start_date <= today <= current.start_date + timedelta(days=6) else None,
        "all_weeks": [week_view(doc, week, today, found, by_key) for week in doc.plan.weeks],
        "versions": plan.history(limit=10),
    }


WELLNESS_SHOWN = ("sleep_h", "sleep_score", "resting_hr", "hrv", "readiness", "fitness", "fatigue", "form", "ramp", "weight_kg", "soreness", "tiredness", "stress", "mood", "motivation")
COACH_STATUS = ("morning_brief", "wellness_signal")


def local_day(iso: str) -> str:
    """The athlete's calendar day of a stream event, whose time is kept in UTC."""
    return datetime.fromisoformat(iso).astimezone().date().isoformat()


def day_card(**args) -> dict:
    """One day in one place: what the coach said that morning, what the athlete reported, what was planned and done."""
    today = clock.today()
    which = str(args.get("date") or "today")
    try:
        day = today if which == "today" else today - timedelta(days=1) if which == "yesterday" else date.fromisoformat(which)
    except ValueError as error:
        raise CardError("A day is YYYY-MM-DD, today or yesterday.") from error
    iso = day.isoformat()
    doc = plan.load()
    found, by_key = matches(doc), {row["key"]: row for row in mirror.rows()}
    sessions = [session_view(s, today, found, by_key) for s in doc.plan.sessions() if s.date == day] if doc else []
    activities = [
        activity_brief(row) | {"athlete_note": note_text(row["key"], "athlete.md"), "planned": bool(found.by_activity.get(row["key"]))}
        for row in by_key.values() if row["date"] == iso
    ]
    wellness = next(iter(mirror.wellness(iso, iso)), {})
    said = [e for e in hub.stream.history(limit=10**9, everything=True) if e["type"] == "message" and local_day(e["at"]) == iso]
    return {
        "date": iso, "is_today": day == today, "previous": (day - timedelta(days=1)).isoformat(),
        "next": (day + timedelta(days=1)).isoformat() if day < today else None,
        "sessions": sessions, "activities": activities,
        "wellness": {key: wellness[key] for key in WELLNESS_SHOWN if wellness.get(key) is not None},
        "wellness_comments": wellness.get("comments") or "",
        "coach": [{"at": e["at"], "origin": e["origin"], "text": e["text"]} for e in said if e.get("role") == "coach" and e.get("origin") in COACH_STATUS],
        "checkins": note_file("checkins", f"{iso}.md"),
        "link": provider_link("wellness", iso),
    }


def proposal_card(id: str = "", **_) -> dict:
    try:
        proposal = plan.load_proposal(id)
    except plan.PlanError as error:
        raise CardError(str(error)) from error
    current = plan.load()
    base = current
    if proposal.status == "approved" and proposal.base_commit:
        try:  # show what it changed, not the nothing it differs from the plan it became
            base = plan.at_version(proposal.base_commit)
        except Exception:
            base = None
    elif proposal.status == "approved":
        base = None
    return {
        "id": proposal.id, "kind": proposal.kind, "status": proposal.status, "rationale": proposal.rationale,
        "changes": [c.model_dump() for c in proposal.changes], "warnings": proposal.warnings, "author": proposal.author,
        "created_at": proposal.created_at, "decided_at": proposal.decided_at, "decision_note": proposal.decision_note,
        "diff": plan.diff(None if proposal.kind == "new_plan" else base, proposal.doc),
        "plan": {
            "goal": proposal.doc.request.goal_description, "race_date": proposal.doc.request.race_date.isoformat(),
            "weeks": len(proposal.doc.plan.weeks), "summary": proposal.doc.plan.summary, "paces": proposal.doc.paces.model_dump(),
        } if proposal.kind == "new_plan" else None,
    }


def report_card(id: str = "", **_) -> dict:
    folder = workspace.root() / "reports" / id
    if not id or "/" in id or not (folder / "report.md").exists():
        raise CardError(f"There is no report {id!r}. `openstride reports` lists them.")
    files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p.name != "report.md")
    return {"id": id, "markdown": (folder / "report.md").read_text(), "files": files}


def table_card(data=None, **_) -> dict:
    data = data or {}
    columns, rows = data.get("columns"), data.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or not all(isinstance(r, list) and len(r) == len(columns) for r in rows):
        raise CardError('A table needs {"title": "...", "columns": ["a", "b"], "rows": [[1, 2], ...]} with every row as long as columns.')
    if len(rows) > 200:
        raise CardError("A table card holds at most 200 rows. Summarise, or put the full table in a report.")
    return {"title": str(data.get("title") or ""), "columns": [str(c) for c in columns], "rows": rows, "note": str(data.get("note") or "")}


def choice_card(data=None, **_) -> dict:
    data = data or {}
    options = data.get("options")
    if not data.get("question") or not isinstance(options, list) or not 2 <= len(options) <= 6:
        raise CardError("A choice needs a question and two to six options.")
    return {"question": str(data["question"]), "options": [str(option) for option in options]}


BUILDERS = {
    "week": week_card, "session": session_card, "activity": activity_card, "activities": activities_card,
    "day": day_card, "plan": plan_card, "proposal": proposal_card, "report": report_card,
    "table": table_card, "choice": choice_card,
}
STATIC = ("table", "choice")  # their data is what the agent computed, kept with the event


def build(card: str, args: dict | None = None, data: dict | None = None) -> dict:
    if card not in BUILDERS:
        raise CardError(f"There is no {card!r} card. Cards: {', '.join(BUILDERS)}.")
    return BUILDERS[card](**(args or {}), **({"data": data} if card in STATIC else {}))


def glance() -> dict:
    """The header: what the athlete reads without asking."""
    today = clock.today()
    doc = plan.load()
    result = {"today": today.isoformat(), "has_plan": doc is not None, "sessions_today": [], "next_hard": None, "days_to_race": None, "form": None, "week": None}
    wellness = [day for day in mirror.wellness() if day.get("form") is not None]
    if wellness:
        result["form"] = {"date": wellness[-1]["date"], "value": wellness[-1]["form"], "fitness": wellness[-1]["fitness"], "fatigue": wellness[-1]["fatigue"]}
    if doc:
        found = matches(doc)
        by_key = {row["key"]: row for row in mirror.rows()}
        result["days_to_race"] = (doc.request.race_date - today).days
        result["goal"] = doc.request.goal_description
        result["sessions_today"] = [session_view(s, today, found, by_key) for s in doc.plan.sessions() if s.date == today]
        ahead = [s for s in doc.plan.sessions() if s.date > today and s.type in HARD_TYPES and s.status == "planned"]
        if ahead:
            first = min(ahead, key=lambda s: s.date)
            result["next_hard"] = plan.brief(first)
        week = doc.plan.week_of(today)
        if week:
            view = week_view(doc, week, today, found, by_key)
            result["week"] = {key: view[key] for key in ("week_number", "of", "phase", "planned_km", "done_km", "sessions_planned", "sessions_done")}
    result["pending_proposals"] = [p.id for p in plan.proposals("pending")]
    return result


def dumps(value) -> str:
    return json.dumps(value, indent=1, default=str)
