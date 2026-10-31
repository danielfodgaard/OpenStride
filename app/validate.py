"""Checks on plans. Errors are facts and the athlete's own constraints, and go back to the LLM.
Warnings are coaching judgments: they never block anything and are shown to the athlete.
"""
from collections import Counter


from app.models import DAYS, AdaptationResult, PlanFile, SavedRequest, TrainingPlan

HARD_TYPES = {"tempo", "intervals", "long"}
MODALITIES = dict.fromkeys(("easy", "long", "tempo", "intervals", "recovery", "race"), "run") | {
    "strength": "strength",
    "cross_training": "cross",
}


def validate_plan(plan: TrainingPlan, request: SavedRequest, n_weeks: int, long_run_rule: bool = True) -> list[str]:
    """Return human-readable errors; empty means valid. In a proposal the long run may move for a week."""
    errors = []

    numbers = [week.week_number for week in plan.weeks]
    if numbers != list(range(1, n_weeks + 1)):
        errors.append(f"Expected weeks numbered 1..{n_weeks}, got {numbers}")

    race_day = DAYS[request.race_date.weekday()]
    allowed_sessions = request.runs_per_week + request.strength_sessions_per_week + request.cross_sessions_per_week

    for index, week in enumerate(plan.weeks):
        label = f"Week {week.week_number}"
        run_days = [session.day for session in week.sessions if session.modality == "run"]
        if len(run_days) > request.runs_per_week:
            errors.append(f"{label} has {len(run_days)} runs, more than runs_per_week ({request.runs_per_week})")
        if len(week.sessions) > allowed_sessions:
            errors.append(f"{label} has {len(week.sessions)} sessions, more than the {allowed_sessions} requested")
        if len(set(run_days)) != len(run_days):
            errors.append(f"{label} has more than one run on the same day")
        if week.planned_km < 0:
            errors.append(f"{label} has negative planned_km")

        for session in week.sessions:
            where = f"{label} {session.day}"
            # The race date is fixed by the race, so it may fall outside the runner's usual days.
            if session.type != "race" and session.day not in request.available_days:
                errors.append(f"{where} is not an available day")
            if long_run_rule and session.type == "long" and session.day != request.long_run_day:
                errors.append(f"{where} is a long run, which must be on {request.long_run_day}")
            if MODALITIES[session.type] != session.modality:
                errors.append(f"{where} of type {session.type} must have modality {MODALITIES[session.type]}")
            if session.distance_km < 0 or session.duration_min < 0:
                errors.append(f"{where} has a negative distance or duration")
            for step in session.steps:
                if step.repeat < 1:
                    errors.append(f"{where} step {step.description!r} has repeat below 1")
                if step.distance_km < 0 or step.duration_min < 0:
                    errors.append(f"{where} step {step.description!r} has a negative distance or duration")

    last_index = len(plan.weeks) - 1
    race_weeks = [i for i, week in enumerate(plan.weeks) for s in week.sessions if s.type == "race"]
    if race_weeks != [last_index]:
        errors.append("The last week must contain exactly one race session and no other week may contain one")
    else:
        final = plan.weeks[-1]
        if not any(s.type == "race" and s.day == race_day for s in final.sessions):
            errors.append(f"The race session must be on {race_day}, the race date's weekday")
        if any(DAYS.index(s.day) > DAYS.index(race_day) for s in final.sessions):
            errors.append(f"The last week has a session after the race on {race_day}")

    return errors


def soft_warnings(plan: TrainingPlan) -> list[str]:
    """Coaching judgments worth telling the athlete about."""
    hard_days = sorted(
        (index * 7 + DAYS.index(session.day), f"Week {week.week_number} {session.day}")
        for index, week in enumerate(plan.weeks)
        for session in week.sessions
        if session.type in HARD_TYPES and session.modality == "run" and getattr(session, "status", "planned") != "skipped"
    )
    warnings = [
        f"Hard runs on consecutive days: {first} and {second}"
        for (day, first), (next_day, second) in zip(hard_days, hard_days[1:])
        if next_day == day + 1
    ]
    for previous, week in zip(plan.weeks, plan.weeks[1:]):
        if previous.planned_km > 0 and week.planned_km > previous.planned_km * 1.15:
            warnings.append(
                f"Week {week.week_number} volume rises more than 15% "
                f"({previous.planned_km:g} to {week.planned_km:g} km)"
            )
    return warnings


def scope_errors(result: AdaptationResult, scope_weeks: list[int]) -> list[str]:
    if result.recommendation == "no_change":
        return ["no_change must have empty weeks"] if result.weeks else []
    numbers = [week.week_number for week in result.weeks]
    if numbers != scope_weeks:
        return [f"Return exactly weeks {scope_weeks}, got {numbers}"]
    return []


def facts(session) -> dict:
    return session.model_dump(exclude={"date", "status", "moved_from", "edit_note"})


def unexplained_removals(old, new, changes, today) -> list[str]:
    """Each session type a week loses needs its own Change entry on the day it was planned."""
    still_planned = [s for s in old.sessions if s.date > today]
    lost = Counter(s.type for s in still_planned) - Counter(s.type for s in new.sessions if s.date > today)
    explained = {change.day for change in changes if change.week_number == old.week_number}
    errors = []
    for kind, count in lost.items():
        days = [s.day for s in still_planned if s.type == kind]
        if sum(day in explained for day in days) < count:
            errors.append(
                f"Week {old.week_number}: a {kind} session ({', '.join(days)}) was removed or downgraded "
                "without its own change entry giving the reason"
            )
    return errors


def validate_merge(plan_file: PlanFile, merged: PlanFile, changes, scope_weeks: list[int], today) -> list[str]:
    """`merged` is the plan with a proposal's weeks swapped in."""
    errors = []
    for old, new in zip(plan_file.plan.weeks, merged.plan.weeks):
        if old.week_number not in scope_weeks:
            continue
        past_old = [facts(s) for s in old.sessions if s.date <= today]
        past_new = [facts(s) for s in new.sessions if s.date <= today]
        if past_old != past_new:
            errors.append(f"Week {old.week_number}: sessions dated {today} or earlier must stay exactly as they are")
        errors += unexplained_removals(old, new, changes, today)

    races = lambda plan: [(s.date, facts(s)) for w in plan.plan.weeks for s in w.sessions if s.type == "race"]
    if races(plan_file) != races(merged):
        errors.append("The race session must stay exactly as it is")

    # Only new problems count: an already-imperfect plan must not block every adaptation.
    n_weeks = len(plan_file.plan.weeks)
    existing = validate_plan(plan_file.plan, plan_file.request, n_weeks, long_run_rule=False)
    return errors + [
        e for e in validate_plan(merged.plan, merged.request, n_weeks, long_run_rule=False) if e not in existing
    ]


def merge_warnings(plan_file: PlanFile, merged: PlanFile) -> list[str]:
    """The trade-offs a proposal adds, to show above its diff."""
    long_days = lambda plan: [
        f"Week {week.week_number}: the long run moves to {session.day}, away from your usual {plan.request.long_run_day}"
        for week in plan.plan.weeks
        for session in week.sessions
        if session.type == "long" and session.day != plan.request.long_run_day
    ]
    existing = [*soft_warnings(plan_file.plan), *long_days(plan_file)]
    return [w for w in [*soft_warnings(merged.plan), *long_days(merged)] if w not in existing]
