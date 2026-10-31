"""Semantic checks on a TrainingPlan that the JSON schema cannot express."""

from app.models import DAYS, Paces, SavedRequest, TrainingPlan

HARD_TYPES = {"tempo", "intervals", "long"}
PACE_ZONES = ("easy", "marathon", "threshold", "interval", "repetition", "race_pace")


def validate_plan(plan: TrainingPlan, request: SavedRequest, paces: Paces, n_weeks: int) -> list[str]:
    """Return human-readable errors; empty means valid."""
    errors = []

    numbers = [week.week_number for week in plan.weeks]
    if numbers != list(range(1, n_weeks + 1)):
        errors.append(f"Expected weeks numbered 1..{n_weeks}, got {numbers}")

    allowed_paces = {getattr(paces, zone) for zone in PACE_ZONES}
    race_day = DAYS[request.race_date.weekday()]
    hard_days = []

    for index, week in enumerate(plan.weeks):
        label = f"Week {week.week_number}"
        days = [session.day for session in week.sessions]
        if len(days) > request.runs_per_week:
            errors.append(f"{label} has {len(days)} sessions, more than runs_per_week ({request.runs_per_week})")
        if len(set(days)) != len(days):
            errors.append(f"{label} has more than one session on the same day")
        if week.planned_km < 0:
            errors.append(f"{label} has negative planned_km")

        for session in week.sessions:
            where = f"{label} {session.day}"
            # The race date is fixed by the race, so it may fall outside the runner's usual days.
            if session.type != "race" and session.day not in request.available_days:
                errors.append(f"{where} is not an available day")
            if session.type == "long" and session.day != request.long_run_day:
                errors.append(f"{where} is a long run, which must be on {request.long_run_day}")
            if session.type in HARD_TYPES:
                hard_days.append((index * 7 + DAYS.index(session.day), where))
            if session.distance_km < 0 or session.duration_min < 0:
                errors.append(f"{where} has a negative distance or duration")
            if session.target_pace not in allowed_paces:
                errors.append(f"{where} target_pace {session.target_pace!r} is not a provided zone")
            for step in session.steps:
                if step.target_pace not in allowed_paces:
                    errors.append(f"{where} step {step.description!r} target_pace {step.target_pace!r} is not a provided zone")
                if step.repeat < 1:
                    errors.append(f"{where} step {step.description!r} has repeat below 1")
                if step.distance_km < 0 or step.duration_min < 0:
                    errors.append(f"{where} step {step.description!r} has a negative distance or duration")

    hard_days.sort()
    for (day, first), (next_day, second) in zip(hard_days, hard_days[1:]):
        if next_day == day + 1:
            errors.append(f"Hard sessions on consecutive days: {first} and {second}")

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
    """Issues worth telling the runner about that don't justify regenerating."""
    warnings = []
    for previous, week in zip(plan.weeks, plan.weeks[1:]):
        if previous.planned_km > 0 and week.planned_km > previous.planned_km * 1.15:
            warnings.append(
                f"Week {week.week_number} volume rises more than 15% "
                f"({previous.planned_km:g} to {week.planned_km:g} km)"
            )
    return warnings
