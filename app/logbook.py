"""Sessions logged by hand: strength, cross-training and watch-less runs."""
from app import activities, clock, lifecycle, metrics, storage
from app.models import ManualActivity, Match, PlanFile

LABELS = {"strength": "Strength", "cross": "Cross-training", "run": "Run"}


def match_entry(entry: ManualActivity, plan_file: PlanFile) -> Match:
    found = activities.closest_session(
        activities.sessions_on(plan_file, entry.date, entry.modality), entry.distance_km
    )
    if found is None:
        return Match()
    return Match(planned_date=entry.date.isoformat(), week_number=found[0], session_index=found[1])


def summary_line(entry: ManualActivity) -> str:
    parts = [f"{LABELS[entry.modality]} {entry.duration_min} min"]
    if entry.distance_km:
        parts.append(f"{entry.distance_km:g} km")
    if entry.rpe:
        parts.append(f"RPE {entry.rpe}")
    if entry.details:
        parts.append(f"{len(entry.details)} exercises")
    return ", ".join(parts)


def as_item(entry: ManualActivity) -> dict:
    return entry.model_dump(mode="json") | {"manual": True, "summary": summary_line(entry), "review": None}


def all_items(plan_id: str) -> list[dict]:
    """Everything done on a plan, oldest first, for LLM context."""
    files = [activities.with_review(a) for a in storage.list_activities(plan_id)]
    return sorted([*files, *(as_item(m) for m in storage.list_manual(plan_id))], key=lambda item: item["date"])


def planned_session(match: Match, plan_file: PlanFile | None):
    if plan_file is None or match.session_index < 0:
        return None
    week = next((w for w in plan_file.plan.weeks if w.week_number == match.week_number), None)
    return week.sessions[match.session_index] if week and match.session_index < len(week.sessions) else None


def workout_item(item: dict, plans: dict) -> dict:
    """An item for the Workouts page: title, and which plan and session it counts for."""
    plan_id = item["plan_id"]
    if plan_id not in plans:
        plans[plan_id] = storage.load_plan(plan_id) if plan_id else None
    plan_file = plans[plan_id]
    session = planned_session(Match(**item["match"]), plan_file)
    title = session.title if session else (item["title"] if item.get("manual") else item["source_file"])
    label = f"{plan_file.request.goal_description} · Week {item['match']['week_number']} {session.day}" if session else "Unplanned"
    return item | {
        "title": title,
        "type": session.type if session else item["modality"],
        "plan_title": plan_file.request.goal_description if plan_file else "",
        "match_label": label,
    }


def workouts(plan_id: str | None = None) -> list[dict]:
    """Every recorded workout, newest first; one plan's only when `plan_id` is given."""
    files = storage.list_activities(plan_id) if plan_id else storage.list_all_activities()
    logged = storage.list_manual(plan_id) if plan_id else storage.list_all_manual()
    items = [*(activities.with_review(a) for a in files), *(as_item(m) for m in logged)]
    plans = {}
    moment = lambda item: item.get("start_time") or item["date"]
    return sorted((workout_item(item, plans) for item in items), key=moment, reverse=True)


def workout(workout_id: str) -> dict:
    """One workout by id, from a file or the logbook, with what its detail page needs."""
    try:
        item = activities.with_review(storage.load_activity(workout_id))
    except storage.NotFound:
        item = as_item(storage.load_manual(workout_id))
    return workout_item(item, {}) | {
        "has_series": storage.has_series(workout_id),
        "has_raw": storage.find_raw(workout_id) is not None,
    }


def check_date(day) -> None:
    if day > clock.today():
        raise lifecycle.Conflict("A session can't be logged for a future date.")


def planned_title(entry: ManualActivity, plan_file: PlanFile) -> str:
    found = activities.sessions_on(plan_file, entry.date, entry.modality)
    return found[0][2].title if found else LABELS[entry.modality]


def create(plan_id: str | None, fields: dict) -> ManualActivity:
    """Against the active plan if there is one and it's the one asked for; otherwise unplanned."""
    if plan_id:
        lifecycle.require_plan(plan_id)
    active = lifecycle.active_plan()
    plan_file = active if active and plan_id in (None, active.meta.id) else None
    check_date(fields["date"])
    owner = plan_file.meta.id if plan_file else ""
    with storage.workout_lock(owner):
        entry = ManualActivity(id=storage.new_manual_id(owner, fields["date"]), plan_id=owner, match=Match(), **fields)
        if plan_file:
            entry.match = match_entry(entry, plan_file)
            entry.title = entry.title.strip() or planned_title(entry, plan_file)
        else:
            entry.title = entry.title.strip() or LABELS[entry.modality]
        storage.save_manual(entry)
        if plan_file:
            metrics.refresh(plan_file)
        return entry


def edit(manual_id: str, changes: dict, match: tuple[int, int] | None) -> ManualActivity:
    plan_file = activities.plan_for_edit(storage.load_manual(manual_id).plan_id)
    with storage.workout_lock(plan_file.meta.id if plan_file else ""):
        entry = storage.load_manual(manual_id)
        moved = any(key in changes and changes[key] != getattr(entry, key) for key in ("date", "modality"))
        if "date" in changes:
            check_date(changes["date"])
        old = entry
        entry = entry.model_copy(update=changes)
        if match is not None:
            week_number, index = match
            if index == -1:
                entry.match = Match()
            else:
                entry.match = activities.planned_match(plan_file, week_number, index, entry.modality)
                if not entry.plan_id:  # joining the plan moves it into that plan's folder
                    entry.plan_id = plan_file.meta.id
                    entry.id = storage.new_manual_id(entry.plan_id, entry.date)
                    storage.delete_manual(old)
        elif moved and entry.plan_id:
            entry.match = match_entry(entry, plan_file)
        storage.save_manual(entry)
        if plan_file:
            metrics.refresh(plan_file)
        return entry


def delete(manual_id: str) -> None:
    plan_file = activities.plan_for_edit(storage.load_manual(manual_id).plan_id)
    with storage.workout_lock(plan_file.meta.id if plan_file else ""):
        storage.delete_manual(storage.load_manual(manual_id))
        if plan_file:
            metrics.refresh(plan_file)
