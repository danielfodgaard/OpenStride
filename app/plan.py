"""The plan in the workspace, and the one way it changes: a draft becomes a proposal, the athlete
approves it, and `apply` writes plan/. Dates, ids and weekly volume are computed here, never taken
from the agent.
"""
import json
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError

from app import clock, gate, matching, mirror, validate, workspace
from app.models import (
    DAYS, DatedPlan, DatedSession, DatedWeek, Draft, Paces, PlanDoc, PlanRequest, Proposal, SavedRequest, Week,
)
from app.vdot import compute_paces

PLAN_FILES = ("plan/plan.json", "plan/request.json", "plan/paces.json")
HELD_FIELDS = ("race_date", "race_distance_km")


class PlanError(Exception):
    """A draft or an approval that can't go through. `problems` are for the agent to fix or the athlete to read."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


def folder():
    return workspace.root() / "plan"


def load() -> PlanDoc | None:
    if not (folder() / "plan.json").exists():
        return None
    return PlanDoc(
        request=SavedRequest.model_validate_json((folder() / "request.json").read_text()),
        paces=Paces.model_validate_json((folder() / "paces.json").read_text()),
        plan=DatedPlan.model_validate_json((folder() / "plan.json").read_text()),
    )


def write(doc: PlanDoc) -> None:
    workspace.write_file(folder() / "plan.json", doc.plan.model_dump_json(indent=2))
    workspace.write_file(folder() / "request.json", doc.request.model_dump_json(indent=2))
    workspace.write_file(folder() / "paces.json", doc.paces.model_dump_json(indent=2))


def plan_commit() -> str:
    """The commit that last wrote the plan itself. Sending it to the calendar doesn't count."""
    return workspace.last_commit(*PLAN_FILES)


def paces_for(request: SavedRequest) -> Paces:
    return Paces(**compute_paces(request.recent_race_distance_km, request.recent_race_time, request.race_distance_km, request.goal_time))


def done_sessions(doc: PlanDoc | None) -> set[str]:
    if doc is None:
        return set()
    return matching.compute(doc.plan, mirror.rows(), matching.overrides(), mirror.event_pairs()).done()


def new_id(taken: set[str]) -> str:
    number = len(taken) + 1
    while f"s{number}" in taken:
        number += 1
    taken.add(f"s{number}")
    return f"s{number}"


def dated(week: Week, start: date, taken: set[str], before: dict[str, DatedSession]) -> DatedWeek:
    """A week as stored: each session on its date, with an id, and the week's running volume added up."""
    sessions = []
    for session in week.sessions:
        day = start + timedelta(days=DAYS.index(session.day))
        fields = session.model_dump()
        if not fields["id"] or (fields["id"] in {s.id for s in sessions}):
            fields["id"] = new_id(taken)
        taken.add(fields["id"])
        old = before.get(fields["id"])
        if old is not None:  # where it first stood, so the athlete and the coach can see what moved
            original = old.moved_from or old.date
            fields["moved_from"] = None if original == day else original
        else:
            fields["moved_from"] = None
        sessions.append(DatedSession(**fields, date=day))
    planned = sum(s.distance_km for s in sessions if s.modality == "run" and s.status != "skipped")
    return DatedWeek(**week.model_dump(exclude={"sessions", "planned_km"}), planned_km=round(planned, 1), start_date=start, sessions=sessions)


def build_new(draft: Draft, today: date) -> tuple[PlanDoc, int]:
    """A plan from scratch, and how many weeks it must have: from the first Monday on or after today through race week."""
    if not draft.request:
        raise PlanError("A new plan needs `request`: what the athlete asked for.", ["`request` is missing from the draft"])
    try:
        request = PlanRequest.model_validate(draft.request)
    except ValidationError as error:
        raise PlanError("The request is not valid.", [f"request.{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]) from error
    starts = clock.week_starts(today, request.race_date)
    taken: set[str] = set()
    weeks = [dated(week, starts[0] + timedelta(weeks=i), taken, {}) for i, week in enumerate(draft.weeks)]
    plan = DatedPlan(summary=draft.summary or "", warnings=draft.warnings or [], weeks=weeks)
    return PlanDoc(request=SavedRequest.model_validate(request.model_dump()), paces=paces_for(request), plan=plan), len(starts)


def build_change(current: PlanDoc, draft: Draft) -> PlanDoc:
    """The current plan with the draft's weeks swapped in by number."""
    request = current.request
    if draft.request:
        try:
            request = SavedRequest.model_validate(current.request.model_dump() | draft.request)
        except ValidationError as error:
            raise PlanError("The changed request is not valid.", [f"request.{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]) from error
    by_number = {week.week_number: week for week in draft.weeks}
    unknown = sorted(set(by_number) - {w.week_number for w in current.plan.weeks})
    if unknown:
        raise PlanError("The draft has weeks the plan doesn't.", [f"The plan has no week {n}" for n in unknown])
    before = {s.id: s for s in current.plan.sessions()}
    kept = {s.id for w in current.plan.weeks if w.week_number not in by_number for s in w.sessions}
    taken = set(before) | kept
    weeks = [
        dated(by_number[week.week_number], week.start_date, taken, before) if week.week_number in by_number else week
        for week in current.plan.weeks
    ]
    plan = DatedPlan(
        summary=draft.summary if draft.summary is not None else current.plan.summary,
        warnings=draft.warnings if draft.warnings is not None else current.plan.warnings,
        weeks=weeks,
    )
    paces = current.paces
    if any(getattr(request, f) != getattr(current.request, f) for f in ("recent_race_distance_km", "recent_race_time", "goal_time")):
        paces = paces_for(request)
    return PlanDoc(request=request, paces=paces, plan=plan)


def check(payload: dict, new: bool, today: date | None = None) -> tuple[PlanDoc, list[str], list[str], Draft]:
    """A draft as the plan it would make, with its errors and warnings. Nothing is stored."""
    today = today or clock.today()
    try:
        draft = Draft.model_validate(payload)
    except ValidationError as error:
        raise PlanError("The draft is not in the plan's shape.", [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()[:20]]) from error
    current = load()
    if new or current is None:
        doc, weeks_expected = build_new(draft, today)
        errors, warnings = validate.new_plan(doc, weeks_expected)
    else:
        doc = build_change(current, draft)
        errors, warnings = validate.change(current, doc, draft.changes, today, done_sessions(current))
    return doc, errors, warnings, draft


def proposals_dir():
    return workspace.root() / "proposals"


def load_proposal(proposal_id: str) -> Proposal:
    path = proposals_dir() / f"{proposal_id}.json"
    if not proposal_id.replace("-", "").replace("_", "").isalnum() or not path.exists():
        raise PlanError(f"There is no proposal {proposal_id!r}.")
    return Proposal.model_validate_json(path.read_text())


def save_proposal(proposal: Proposal) -> None:
    workspace.write_file(proposals_dir() / f"{proposal.id}.json", proposal.model_dump_json(indent=2))


def proposals(status: str | None = None) -> list[Proposal]:
    found = [Proposal.model_validate_json(path.read_text()) for path in sorted(proposals_dir().glob("*.json"), reverse=True)]
    return [p for p in found if status is None or p.status == status]


def draft(payload: dict, new: bool = False, author: str = "agent", today: date | None = None) -> Proposal:
    """Validate a draft and put it in front of the athlete. The plan does not change."""
    doc, errors, warnings, parsed = check(payload, new, today)
    if errors:
        raise PlanError("The draft has problems. Fix them and draft again.", errors)
    if not parsed.rationale.strip():
        raise PlanError("A proposal needs a rationale: two to four sentences the athlete reads before deciding.", ["`rationale` is empty"])
    current = load()
    is_new = new or current is None
    if not is_new and doc.model_dump() == current.model_dump():
        raise PlanError("The draft is the same as the current plan; there is nothing to propose.", ["No session differs from the plan"])
    now = datetime.now(timezone.utc)
    with workspace.lock():
        stem = f"{now:%Y-%m-%dT%H-%M-%S}_{'new-plan' if is_new else 'change'}"
        name, suffix = stem, 1
        while (proposals_dir() / f"{name}.json").exists():
            suffix += 1
            name = f"{stem}-{suffix}"
        proposal = Proposal(
            id=name, created_at=now.isoformat(timespec="seconds"), kind="new_plan" if is_new else "change",
            base_commit=plan_commit(), rationale=parsed.rationale.strip(), changes=parsed.changes, warnings=warnings,
            author=author, doc=doc,
        )
        save_proposal(proposal)
        workspace.commit_protected(f"Proposal {name}: {proposal.rationale.splitlines()[0][:80]}", ["proposals"])
    return proposal


def approval_token(proposal: Proposal) -> str:
    return gate.token(proposal.id, proposal.base_commit)


def apply(proposal_id: str, token: str, today: date | None = None) -> PlanDoc:
    """The gate. Writes plan/ only with the athlete's approval of this proposal against this plan."""
    today = today or clock.today()
    with workspace.lock():
        proposal = load_proposal(proposal_id)
        if not gate.valid(token, proposal.id, proposal.base_commit):
            raise PlanError("That is not a valid approval for this proposal. Only the athlete's Approve applies a plan.")
        if proposal.status != "pending":
            raise PlanError(f"This proposal is already {proposal.status}.")
        current = load()

        def stale(reason: str, problems: list[str] | None = None):
            proposal.status, proposal.decided_at, proposal.decision_note = "stale", workspace.now_iso(), reason
            save_proposal(proposal)
            workspace.commit_protected(f"Proposal {proposal.id} is out of date", ["proposals"])
            return PlanError(reason, problems)

        if proposal.base_commit != plan_commit():
            raise stale("The plan has changed since this proposal was made. Ask for it again.")
        if proposal.kind == "change":
            errors, _ = validate.change(current, proposal.doc, proposal.changes, today, done_sessions(current))
        else:
            errors = validate.facts(proposal.doc.plan, proposal.doc.request)
            if proposal.doc.request.race_date < today:
                errors.append("The race date has passed")
        if errors:  # days pass between proposing and approving, so a session it moves may be in the past by now
            raise stale("This proposal is out of date: " + " ".join(errors), errors)

        write(proposal.doc)
        proposal.status, proposal.decided_at = "approved", workspace.now_iso()
        save_proposal(proposal)
        for other in proposals("pending"):
            other.status, other.decided_at, other.decision_note = "stale", workspace.now_iso(), "Another proposal was approved first."
            save_proposal(other)
        headline = proposal.rationale.splitlines()[0][:100]
        workspace.commit_protected(f"Plan ({'new plan' if proposal.kind == 'new_plan' else 'change'}, approved): {headline}", ["plan", "proposals"])
        return proposal.doc


def reject(proposal_id: str, note: str = "") -> Proposal:
    with workspace.lock():
        proposal = load_proposal(proposal_id)
        if proposal.status != "pending":
            raise PlanError(f"This proposal is already {proposal.status}.")
        proposal.status, proposal.decided_at, proposal.decision_note = "rejected", workspace.now_iso(), note.strip()
        save_proposal(proposal)
        workspace.commit_protected(f"Proposal {proposal.id} rejected", ["proposals"])
        return proposal


def brief(session) -> dict:
    """A session as a diff or a card shows it."""
    return {
        "id": session.id, "date": session.date.isoformat(), "day": session.day, "type": session.type, "modality": session.modality,
        "title": session.title, "distance_km": session.distance_km, "duration_min": session.duration_min,
        "target_pace": session.target_pace, "status": session.status,
    }


def diff(old: PlanDoc | None, new: PlanDoc) -> list[dict]:
    """Per week, what a proposal adds, removes, moves and changes. Sessions are followed by id."""
    before = {s.id: s for s in old.plan.sessions()} if old else {}
    after = {s.id: s for s in new.plan.sessions()}
    weeks = []
    for week in new.plan.weeks:
        old_week = old.plan.week(week.week_number) if old else None
        entries = []
        for session in week.sessions:
            was = before.get(session.id)
            if was is None:
                entries.append({"kind": "added", "after": brief(session)})
                continue
            fields = [
                name for name in ("type", "title", "distance_km", "duration_min", "target_pace", "description", "steps", "status")
                if getattr(was, name) != getattr(session, name)
            ]
            moved = was.date != session.date
            if moved or fields:
                kind = "moved" if moved and not fields else "skipped" if fields == ["status"] and session.status == "skipped" else "changed"
                entries.append({"kind": kind, "before": brief(was), "after": brief(session), "fields": fields, "moved": moved})
        for session in (old_week.sessions if old_week else []):
            if session.id not in after:
                entries.append({"kind": "removed", "before": brief(session)})
        if entries:
            entries.sort(key=lambda e: (e.get("after") or e["before"])["date"])
            weeks.append({
                "week_number": week.week_number, "start_date": week.start_date.isoformat(), "phase": week.phase,
                "planned_km_before": old_week.planned_km if old_week else None, "planned_km_after": week.planned_km, "entries": entries,
            })
    return weeks


def history(limit: int = 50) -> list[dict]:
    return workspace.log("plan/plan.json", limit=limit)


def at_version(commit: str) -> PlanDoc:
    return PlanDoc(
        request=SavedRequest.model_validate_json(workspace.show(commit, "plan/request.json")),
        paces=Paces.model_validate_json(workspace.show(commit, "plan/paces.json")),
        plan=DatedPlan.model_validate_json(workspace.show(commit, "plan/plan.json")),
    )


def as_draft(doc: PlanDoc, rationale: str, weeks: list[int] | None = None) -> dict:
    """A plan (or some of its weeks) in the shape `draft` takes: the starting point for an edit."""
    chosen = [w for w in doc.plan.weeks if weeks is None or w.week_number in weeks]
    return {
        "rationale": rationale, "changes": [],
        "weeks": [json.loads(Week.model_validate(w.model_dump()).model_dump_json()) for w in chosen],
    }
