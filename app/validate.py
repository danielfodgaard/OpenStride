"""Checks on plans. Errors are facts: a plan that has one is refused and goes back to the agent.
Warnings are coaching judgments and the athlete's own stated constraints: they never block a change,
and are shown to the athlete next to it.

A brand-new plan is held to the request strictly, because the athlete just stated it. A change to a
running plan is not: the athlete approves each one, so "Wed isn't one of your days" is theirs to accept.
"""
from collections import Counter
from datetime import date, timedelta

from app.models import DAYS, MODALITIES, Change, DatedPlan, PlanDoc, SavedRequest

HARD_TYPES = {"tempo", "intervals", "long"}


def facts(plan: DatedPlan, request: SavedRequest) -> list[str]:
    """What must hold for any plan, whoever wrote it."""
    errors = []
    numbers = [week.week_number for week in plan.weeks]
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"Weeks must be numbered 1..{len(numbers)} in order, got {numbers}")
    ids = Counter(s.id for s in plan.sessions() if s.id)
    errors += [f"Session id {key!r} is used {count} times" for key, count in ids.items() if count > 1]

    for week in plan.weeks:
        label = f"Week {week.week_number}"
        run_days = [s.day for s in week.sessions if s.modality == "run" and s.status != "skipped"]
        if len(set(run_days)) != len(run_days):
            errors.append(f"{label} has more than one run on the same day")
        for session in week.sessions:
            where = f"{label} {session.day}"
            if MODALITIES[session.type] != session.modality:
                errors.append(f"{where} of type {session.type} must have modality {MODALITIES[session.type]}")
            if session.distance_km < 0 or session.duration_min < 0:
                errors.append(f"{where} has a negative distance or duration")
            if not session.title.strip():
                errors.append(f"{where} has no title")
            for step in session.steps:
                if step.repeat < 1:
                    errors.append(f"{where} step {step.description!r} has repeat below 1")
                if step.distance_km < 0 or step.duration_min < 0:
                    errors.append(f"{where} step {step.description!r} has a negative distance or duration")

    races = [s for s in plan.sessions() if s.type == "race"]
    if len(races) != 1:
        errors.append(f"A plan has exactly one race session, this one has {len(races)}")
    elif races[0].date != request.race_date:
        errors.append(f"The race session must be on the race date, {request.race_date} ({DAYS[request.race_date.weekday()]})")
    else:
        if plan.weeks and races[0] not in plan.weeks[-1].sessions:
            errors.append("The race session must be in the last week")
        after = [s for s in plan.sessions() if s.date > request.race_date]
        if after:
            errors.append(f"Nothing may be scheduled after the race; found {len(after)} session(s)")
    return errors


def constraints(plan: DatedPlan, request: SavedRequest) -> list[str]:
    """What the athlete asked for when the plan was made."""
    found = []
    for week in plan.weeks:
        label = f"Week {week.week_number}"
        live = [s for s in week.sessions if s.status != "skipped"]
        runs = [s for s in live if s.modality == "run"]
        if len(runs) > request.runs_per_week:
            found.append(f"{label} has {len(runs)} runs, more than the {request.runs_per_week} per week you asked for")
        for kind, limit, name in (
            ("strength", request.strength_sessions_per_week, "strength"),
            ("cross", request.cross_sessions_per_week, "cross-training"),
        ):
            count = sum(s.modality == kind for s in live)
            if count > limit:
                found.append(f"{label} has {count} {name} sessions, more than the {limit} per week you asked for")
        for session in live:
            # The race date is fixed by the race, so it may fall outside the runner's usual days.
            if session.type != "race" and session.day not in request.available_days:
                found.append(f"{label} {session.day} ({session.title}) is not one of your usual training days")
            if session.type == "long" and session.day != request.long_run_day:
                found.append(f"{label}: the long run is on {session.day}, away from your usual {request.long_run_day}")
    return found


def judgments(plan: DatedPlan) -> list[str]:
    """Coaching judgments worth telling the athlete about."""
    hard_days = sorted(
        (s.date, f"Week {week.week_number} {s.day}")
        for week in plan.weeks
        for s in week.sessions
        if s.type in HARD_TYPES and s.modality == "run" and s.status != "skipped"
    )
    warnings = [
        f"Hard runs on consecutive days: {first} and {second}"
        for (day, first), (next_day, second) in zip(hard_days, hard_days[1:])
        if next_day == day + timedelta(days=1)
    ]
    for previous, week in zip(plan.weeks, plan.weeks[1:]):
        if previous.planned_km > 0 and week.planned_km > previous.planned_km * 1.15:
            warnings.append(
                f"Week {week.week_number} volume rises more than 15% "
                f"({previous.planned_km:g} to {week.planned_km:g} km)"
            )
    strength_days = {s.date for s in plan.sessions() if s.modality == "strength" and s.status != "skipped"}
    for s in plan.sessions():
        if s.type in ("long", "race") and s.date - timedelta(days=1) in strength_days:
            warnings.append(f"Strength the day before the {s.type} on {s.date:%a %d %b}")
    return warnings


def new_plan(doc: PlanDoc, weeks_expected: int) -> tuple[list[str], list[str]]:
    """Errors and warnings for a plan made from scratch."""
    errors = facts(doc.plan, doc.request)
    if len(doc.plan.weeks) != weeks_expected:
        errors.append(f"Expected {weeks_expected} weeks from the first Monday through the race week, got {len(doc.plan.weeks)}")
    return errors + constraints(doc.plan, doc.request), judgments(doc.plan)


def session_facts(session) -> dict:
    return session.model_dump(exclude={"status", "moved_from", "edit_note"})


def frozen(session, today: date, done: set[str]) -> bool:
    """Already happened: before today, or matched to something the athlete did."""
    return session.date < today or session.id in done


def unexplained_removals(old, new, changes: list[Change], today: date, done: set[str]) -> list[str]:
    """Each session type a week loses needs its own change entry on the day it was planned."""
    ahead = [s for s in old.sessions if not frozen(s, today, done) and s.status == "planned"]
    kept = Counter(s.type for s in new.sessions if s.status == "planned")
    lost = Counter(s.type for s in ahead) - kept
    explained = {change.day for change in changes if change.week == old.week_number}
    errors = []
    for kind, count in lost.items():
        days = [s.day for s in ahead if s.type == kind]
        if sum(day in explained for day in days) < count:
            errors.append(
                f"Week {old.week_number}: a {kind} session ({', '.join(days)}) is removed, skipped or downgraded "
                "without its own entry in `changes` on that day giving the reason"
            )
    return errors


def change(old: PlanDoc, new: PlanDoc, changes: list[Change], today: date, done: set[str] = frozenset()) -> tuple[list[str], list[str]]:
    """Errors and warnings for a change to the running plan. Only new problems count:
    an already-imperfect plan must not block every later change."""
    errors = []
    if new.request.race_date != old.request.race_date or new.request.race_distance_km != old.request.race_distance_km:
        errors.append("The race date and distance belong to the plan. For a different race, draft a new plan with --new.")
    if [w.week_number for w in new.plan.weeks] != [w.week_number for w in old.plan.weeks]:
        errors.append("A change keeps the plan's weeks; it can't add or remove one. For that, draft a new plan with --new.")
        return errors, []

    old_sessions = {s.id: s for s in old.plan.sessions()}
    new_sessions = {s.id: s for s in new.plan.sessions()}
    for key, before in old_sessions.items():
        after = new_sessions.get(key)
        where = f"{before.date:%a %d %b} ({before.title})"
        if frozen(before, today, done):
            if after is None or session_facts(after) != session_facts(before):
                errors.append(f"{where} has already happened; only its status and note can change")
        elif before.type == "race" and (after is None or after.model_dump() != before.model_dump()):
            errors.append("The race session must stay exactly as it is")
    for key, after in new_sessions.items():
        before = old_sessions.get(key)
        if before is not None and frozen(before, today, done):
            continue
        if after.date < today and (before is None or before.date != after.date):
            errors.append(f"{after.title} can't move to or be added on {after.date:%a %d %b}: that day has passed")
    for old_week, new_week in zip(old.plan.weeks, new.plan.weeks):
        errors += unexplained_removals(old_week, new_week, changes, today, done)

    existing = facts(old.plan, old.request)
    errors += [e for e in facts(new.plan, new.request) if e not in existing]
    before_warnings = constraints(old.plan, old.request) + judgments(old.plan)
    warnings = [w for w in constraints(new.plan, new.request) + judgments(new.plan) if w not in before_warnings]
    return errors, warnings
