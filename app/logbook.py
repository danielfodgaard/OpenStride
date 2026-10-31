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
    """Everything done on a plan, oldest first, for the page and for LLM context."""
    files = [activities.with_review(a) for a in storage.list_activities(plan_id)]
    return sorted([*files, *(as_item(m) for m in storage.list_manual(plan_id))], key=lambda item: item["date"])


def check_date(day) -> None:
    if day > clock.today():
        raise lifecycle.Conflict("A session can't be logged for a future date.")


def planned_title(entry: ManualActivity, plan_file: PlanFile) -> str:
    found = activities.sessions_on(plan_file, entry.date, entry.modality)
    return found[0][2].title if found else LABELS[entry.modality]


def create(plan_id: str, fields: dict) -> ManualActivity:
    plan_file = lifecycle.require_active(plan_id)
    check_date(fields["date"])
    entry = ManualActivity(
        id=storage.new_manual_id(plan_id, fields["date"]),
        plan_id=plan_id,
        match=Match(),
        **fields,
    )
    entry.match = match_entry(entry, plan_file)
    entry.title = entry.title.strip() or planned_title(entry, plan_file)
    storage.save_manual(entry)
    metrics.refresh(plan_file)
    return entry


def edit(manual_id: str, changes: dict, match: tuple[int, int] | None) -> ManualActivity:
    entry = storage.load_manual(manual_id)
    plan_file = lifecycle.require_active(entry.plan_id)
    moved = any(key in changes and changes[key] != getattr(entry, key) for key in ("date", "modality"))
    if "date" in changes:
        check_date(changes["date"])
    entry = entry.model_copy(update=changes)
    if match is not None:
        week_number, index = match
        entry.match = Match() if index == -1 else activities.planned_match(plan_file, week_number, index)
    elif moved:
        entry.match = match_entry(entry, plan_file)
    storage.save_manual(entry)
    metrics.refresh(plan_file)
    return entry


def delete(manual_id: str) -> None:
    entry = storage.load_manual(manual_id)
    plan_file = lifecycle.require_active(entry.plan_id)
    storage.delete_manual(entry)
    metrics.refresh(plan_file)
