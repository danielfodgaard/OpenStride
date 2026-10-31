"""Planned sessions to Garmin, remembering what was sent in garmin_workouts.json.

One entry per session, keyed "<week>/<index>": workout_id, hash, scheduled_date.
"""
import json
from pathlib import Path

from app import clock, metrics, storage, workout_fit
from app.garmin import provider as garmin
from app.models import PlanFile


def sent_path(plan_id: str) -> Path:
    return storage.plan_dir(plan_id) / "garmin_workouts.json"


def load_sent(plan_id: str) -> dict:
    path = sent_path(plan_id)
    return json.loads(path.read_text()) if path.exists() else {}


def key_of(week: int, index: int) -> str:
    return f"{week}/{index}"


def session_at(plan_file: PlanFile, week: int, index: int):
    found = next((w for w in plan_file.plan.weeks if w.week_number == week), None)
    if found is None or not 0 <= index < len(found.sessions):
        raise storage.NotFound(key_of(week, index))
    return found.sessions[index]


def replace_old(provider, old: dict | None, notes: list[str]) -> None:
    """After the new one is safe. If this fails both stay on Garmin, so say so."""
    if not old:
        return
    try:
        provider.delete_workout(old["workout_id"])
    except garmin.GarminError as error:
        notes.append(f"The previous Garmin workout {old['workout_id']} is still there: {error}")


def send_session(provider, plan_file: PlanFile, week: int, index: int) -> dict:
    session = session_at(plan_file, week, index)
    built = workout_fit.build(session)
    workout_id = provider.push_workout(built.data, workout_fit.garmin_json(session), session.date)
    # TODO(v4 merge): hold storage.plan_lock(plan_id) from this read through the write.
    sent = load_sent(plan_file.meta.id)
    old = sent.get(key_of(week, index))
    sent[key_of(week, index)] = {
        "workout_id": workout_id,
        "hash": workout_fit.fingerprint(session),
        "scheduled_date": session.date.isoformat(),
    }
    garmin.write_json(sent_path(plan_file.meta.id), sent)
    replace_old(provider, old, built.notes)
    return {"workout_id": workout_id, "scheduled_date": session.date.isoformat(), "notes": built.notes}


def week_of(plan_file: PlanFile, number: int):
    week = next((w for w in plan_file.plan.weeks if w.week_number == number), None)
    if week is None:
        raise storage.NotFound(str(number))
    return week


def sendable(plan_file: PlanFile, number: int) -> list[int]:
    """Run sessions still ahead: a session in the past or already done has no use on the watch."""
    done = {(e.match.week_number, e.match.session_index) for e in metrics.entries(plan_file.meta.id)}
    return [
        index for index, session in enumerate(week_of(plan_file, number).sessions)
        if session.modality == "run" and session.date >= clock.today() and (number, index) not in done
    ]


def stale(plan_file: PlanFile, number: int, sent: dict) -> tuple[list[int], list[str]]:
    """Sessions that differ from what Garmin has, and entries whose session has gone from the plan."""
    week = week_of(plan_file, number)
    ahead = sendable(plan_file, number)
    changed = [
        index for index in ahead
        if key_of(number, index) in sent and sent[key_of(number, index)]["hash"] != workout_fit.fingerprint(week.sessions[index])
    ]
    today = clock.today().isoformat()
    runs = {key_of(number, index) for index, session in enumerate(week.sessions) if session.modality == "run"}
    gone = [
        key for key, entry in sent.items()
        if key.startswith(f"{number}/") and key not in runs and entry["scheduled_date"] >= today
    ]
    return changed, gone


def week_summary(plan_file: PlanFile, number: int) -> dict:
    sent = load_sent(plan_file.meta.id)
    changed, gone = stale(plan_file, number, sent)
    return {"sent": sum(key.startswith(f"{number}/") for key in sent), "changed": len(changed) + len(gone)}


def send_week(provider, plan_file: PlanFile, number: int, changed_only: bool) -> dict:
    week = week_of(plan_file, number)
    changed, gone = stale(plan_file, number, load_sent(plan_file.meta.id))
    result = {"sent": 0, "failed": [], "notes": []}
    for index in changed if changed_only else sendable(plan_file, number):
        try:
            result["notes"] += send_session(provider, plan_file, number, index)["notes"]
            result["sent"] += 1
        except garmin.GarminError as error:
            result["failed"].append({"session": week.sessions[index].title, "error": str(error)})
    for key in gone:
        try:
            provider.delete_workout(load_sent(plan_file.meta.id)[key]["workout_id"])
        except garmin.GarminError as error:
            result["failed"].append({"session": f"Removed session {key}", "error": str(error)})
            continue
        sent = load_sent(plan_file.meta.id)  # TODO(v4 merge): plan_lock around this read and write
        del sent[key]
        garmin.write_json(sent_path(plan_file.meta.id), sent)
    return result
