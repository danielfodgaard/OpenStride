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
    session = plan_file.session_at(week, index)
    if session is None:
        raise storage.NotFound(key_of(week, index))
    return session


def week_of(plan_file: PlanFile, number: int):
    week = plan_file.week(number)
    if week is None:
        raise storage.NotFound(str(number))
    return week


def forget(plan_id: str, key: str) -> None:
    """After Garmin deleted the workout, the mapping lets go of it too."""
    with storage.plan_lock(plan_id):
        sent = load_sent(plan_id)
        sent.pop(key, None)
        storage.write_json(sent_path(plan_id), sent)


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
    _, notes = workout_fit.steps_of(session)
    workout_id = provider.push_workout(workout_fit.garmin_json(session), session.date)
    with storage.plan_lock(plan_file.meta.id):
        sent = load_sent(plan_file.meta.id)
        old = sent.get(key_of(week, index))
        sent[key_of(week, index)] = {
            "workout_id": workout_id,
            "hash": workout_fit.fingerprint(session),
            "scheduled_date": session.date.isoformat(),
        }
        storage.write_json(sent_path(plan_file.meta.id), sent)
    replace_old(provider, old, notes)
    return {"workout_id": workout_id, "scheduled_date": session.date.isoformat(), "notes": notes}


def follow_edit(plan_file: PlanFile, moved: dict, removed: list[str]) -> list[str]:
    """After a hand edit: the mapping follows the sessions, and Garmin's calendar follows the dates."""
    plan_id = plan_file.meta.id
    with storage.plan_lock(plan_id):
        sent = {moved.get(key, key): entry for key, entry in load_sent(plan_id).items()}
        if sent:
            storage.write_json(sent_path(plan_id), sent)

    def session_of(key):
        return session_at(plan_file, *map(int, key.split("/")))

    resend = [key for key in moved.values() if key in sent and session_of(key).date.isoformat() != sent[key]["scheduled_date"]]
    gone = [key for key in removed if key in sent]
    if not resend and not gone:
        return []
    if not garmin.status()["can_push_workouts"]:
        return ["Garmin still has the old copy. Delete it there, and download the workout .fit again if you need it."]
    provider = garmin.make_pusher()
    problems = []
    for key in resend:
        try:
            send_session(provider, plan_file, *map(int, key.split("/")))
        except garmin.GarminError as error:
            problems.append(f"Couldn't move it on Garmin: {error}")
    for key in gone:
        try:
            provider.delete_workout(sent[key]["workout_id"])
            forget(plan_id, key)
        except garmin.GarminError as error:
            problems.append(f"Couldn't remove it from Garmin: {error}")
    return problems


def sendable(plan_file: PlanFile, number: int) -> list[int]:
    """Run sessions still ahead: a session in the past or already done has no use on the watch."""
    done = {(e.match.week_number, e.match.session_index) for e in metrics.entries(plan_file.meta.id)}
    return [
        index for index, session in enumerate(week_of(plan_file, number).sessions)
        if session.modality == "run" and session.status == "planned" and session.date >= clock.today() and (number, index) not in done
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
    sent = load_sent(plan_file.meta.id)
    changed, gone = stale(plan_file, number, sent)
    result = {"sent": 0, "failed": [], "notes": []}
    for index in changed if changed_only else sendable(plan_file, number):
        try:
            result["notes"] += send_session(provider, plan_file, number, index)["notes"]
            result["sent"] += 1
        except garmin.GarminError as error:
            result["failed"].append({"session": week.sessions[index].title, "error": str(error)})
    for key in gone:
        try:
            provider.delete_workout(sent[key]["workout_id"])
            forget(plan_file.meta.id, key)
        except garmin.GarminError as error:
            result["failed"].append({"session": f"Removed session {key}", "error": str(error)})
    return result
