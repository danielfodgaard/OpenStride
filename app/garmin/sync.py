"""Garmin activities into the normal import pipeline, by hand or on a timer."""
import logging
import threading
from datetime import datetime, time, timedelta, timezone

from app import activities, lifecycle, logbook, metrics, storage
from app.config import settings
from app.garmin import provider as garmin
from app.models import PlanFile

log = logging.getLogger(__name__)

MIN_SYNC_MINUTES = 15
PLAN_LEAD_IN = timedelta(days=7)
NO_PLAN_LOOKBACK = timedelta(days=30)
# A watch can upload a run days after it was recorded, so sync never trusts last_sync_at exactly.
# Beyond that gap a run is missed; TODO(v4.1) a full-history sync button.
LATE_UPLOAD_OVERLAP = timedelta(days=3)
STRENGTH_SPORT = "strength_training"

sync_lock = threading.Lock()  # the timer and the button must not import the same run twice
stop_event = threading.Event()
worker = None


def since_for(state: dict, plan_file: PlanFile | None) -> datetime:
    now = datetime.now(timezone.utc)
    floors = []
    if state.get("last_sync_at"):
        floors.append(datetime.fromisoformat(state["last_sync_at"]) - LATE_UPLOAD_OVERLAP)
    if plan_file:
        floors.append(datetime.combine(plan_file.plan.weeks[0].start_date, time.min, timezone.utc) - PLAN_LEAD_IN)
    return max(floors) if floors else now - NO_PLAN_LOOKBACK


def local_start(item: garmin.RemoteActivity) -> datetime:
    return item.local_start or item.start_time.astimezone().replace(tzinfo=None)


def log_strength(item: garmin.RemoteActivity, plan_file: PlanFile | None):
    """logbook refuses a future date; the caller leaves it for a later sync, when it no longer is."""
    return logbook.create(plan_file.meta.id if plan_file else None, {
        "date": local_start(item).date(),
        "modality": "strength",
        "title": "",
        "duration_min": round(item.duration_s / 60),
        "distance_km": 0.0,
        "rpe": 0,
        "note": "",
        "details": [],
    })


def import_summary(item: garmin.RemoteActivity, external_id: str, plan_file: PlanFile | None) -> dict:
    """C2 fallback when the file has nothing to read: the listing's facts, marked partial."""
    name = f"garmin_{item.id}"
    local = local_start(item)
    offset = local - item.start_time.replace(tzinfo=None)
    parsed = activities.Parsed(
        points=[],
        sport="running" if "running" in item.sport else item.sport,  # trail_running and the like
        start_time=item.start_time,
        utc_offset=offset if abs(offset) <= activities.MAX_UTC_OFFSET else None,
        distance_km=item.distance_m / 1000,
        moving_time_s=item.duration_s,
        avg_hr=item.avg_hr,
        max_hr=item.max_hr,
    )
    plan_id = plan_file.meta.id if plan_file else ""
    activity = activities.summarise(parsed, name, plan_id, "", 0)
    with storage.workout_lock(plan_id):
        if activities.find_duplicate(activity):
            return {"status": "duplicate"}
        activity.id = storage.new_activity_id(activity.id)
        activity.external_id = external_id
        activity.partial = True
        if plan_file:
            activity.match = activities.match_activity(activity, plan_file)
        storage.save_activity(activity)
        if plan_file:
            metrics.refresh(plan_file)
    warnings = [*activities.date_warnings(activity), f"{name}: the file had no usable data, so only the summary was imported."]
    return {"file": name, "status": "imported", "activity": activity.model_dump(mode="json"), "warnings": warnings, "summary": activity}


def import_remote(provider, item: garmin.RemoteActivity, external_id: str, plan_file: PlanFile | None) -> dict:
    if item.sport == STRENGTH_SPORT:
        entry = log_strength(item, plan_file)
        return {"status": "imported", "activity": entry.model_dump(mode="json"), "warnings": []}
    result = activities.import_fit_bytes(provider.download_fit(item.id), source="garmin", external_id=external_id)
    if result["status"] == "error":
        return import_summary(item, external_id, plan_file)
    if result["status"] == "imported" and not result["activity"]["laps"] and not storage.has_series(result["activity"]["id"]):
        with storage.activities_lock():
            activity = storage.load_activity(result["activity"]["id"])  # totals only: nothing to chart
            activity.partial = True
            storage.save_activity(activity)
    return result


def sync_with(provider) -> dict:
    result = {"imported": [], "skipped": 0, "matched": 0, "unplanned": 0, "warnings": []}
    state = garmin.load_state()
    plan_file = lifecycle.active_plan()

    def record_error(text: str) -> None:
        state["last_error"] = text
        garmin.write_json(garmin.state_path(), state)
        result["warnings"].append(text)
        log.warning("Garmin sync: %s", text)

    try:
        remote = sorted(provider.list_activities(since_for(state, plan_file)), key=lambda item: item.start_time)
    except Exception as error:  # a bad sync must never take the server down
        record_error(garmin.safe_text(error))
        return result

    # The state file also covers logged strength sessions and workouts you deleted here on purpose.
    done = set(state.get("imported", []))
    known = done | {activity.external_id for activity in storage.list_all_activities()}
    failed = 0
    imported = []
    for item in remote:  # oldest first
        external_id = f"garmin:{item.id}"
        if external_id in known:
            result["skipped"] += 1
            continue
        try:
            outcome = import_remote(provider, item, external_id, plan_file)
        except Exception as error:
            failed += 1
            text = str(error) if isinstance(error, lifecycle.Conflict) else garmin.safe_text(error)
            result["warnings"].append(f"Garmin activity {item.id} was not imported: {text}")
            continue
        done.add(external_id)
        state["imported"] = sorted(done)
        garmin.write_json(garmin.state_path(), state)
        if outcome["status"] != "imported":
            result["skipped"] += 1
            continue
        activity = outcome["activity"]
        result["imported"].append(activity["id"])
        result["matched" if activity["match"]["session_index"] >= 0 else "unplanned"] += 1
        result["warnings"] += outcome["warnings"]
        imported.append(outcome)

    # All saved before any review, or a review sees the rest of the batch as missed sessions.
    for outcome in imported:
        activities.add_review(outcome)
        if "message" in outcome:
            result["warnings"].append(outcome["message"])

    if failed:  # last_sync_at stays put, so the next sync reaches these again
        record_error(f"{failed} of {len(remote)} Garmin activities could not be imported. They will be tried again.")
        return result
    state |= {"last_sync_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "last_error": None}
    garmin.write_json(garmin.state_path(), state)
    return result


def sync(provider=None) -> dict:
    if not sync_lock.acquire(blocking=False):
        return {"imported": [], "skipped": 0, "matched": 0, "unplanned": 0, "warnings": ["A Garmin sync is already running."]}
    try:
        result = sync_with(provider or garmin.make_provider())
        log.info("Garmin sync: %d imported, %d skipped, %d warnings", len(result["imported"]), result["skipped"], len(result["warnings"]))
        return result
    finally:
        sync_lock.release()


def loop(seconds: float) -> None:
    while not stop_event.wait(seconds):
        try:
            sync()
        except Exception as error:
            log.error("Garmin background sync failed: %s", garmin.safe_text(error))


def sync_setup_text() -> str:
    mode = garmin.sync_mode()
    if mode == "off":
        return "sync=off"
    key = garmin.personal_key()
    if not key:
        return f"sync={mode} (KEY MISSING: set {garmin.PERSONAL_KEY_ENV} in .env)"
    try:
        garmin.PersonalApiProvider().call(lambda api: None)
    except garmin.GarminError as error:
        return f"sync={mode} (key set, {len(key)} chars, TOKEN REQUEST FAILED: {error})"
    return f"sync={mode} (key set, {len(key)} chars)"


def push_setup_text() -> str:
    mode = garmin.push_mode()
    if mode == "off":
        return "push=off"
    blocked = garmin.push_refusal()
    if blocked:
        return f"push={mode} (REFUSED: {blocked})"
    return f"push={mode} ({'logged in' if garmin.has_secret(garmin.UNOFFICIAL_TOKEN_FILE) else 'not logged in'})"


def setup_line() -> str:
    minutes = settings.garmin_sync_minutes
    auto = f"auto-sync every {max(minutes, MIN_SYNC_MINUTES)} min" if minutes > 0 and garmin.sync_mode() != "off" else "auto-sync off"
    return f"Garmin: {sync_setup_text()}, {push_setup_text()}, {auto}"


def log_setup() -> None:
    if settings.garmin_mode_used:
        log.warning("GARMIN_MODE is the old setting: use GARMIN_SYNC and GARMIN_PUSH in .env instead.")
    try:
        log.info(setup_line())
    except Exception as error:  # only a report: it must never stop the server
        log.warning("Garmin: could not check the setup: %s", garmin.safe_text(error))


def start() -> None:
    global worker
    # In a thread: asking Garmin for a token must not hold up the server starting.
    threading.Thread(target=log_setup, name="garmin-setup", daemon=True).start()
    if settings.garmin_sync_minutes <= 0 or garmin.sync_mode() == "off":
        return
    stop_event.clear()
    minutes = max(settings.garmin_sync_minutes, MIN_SYNC_MINUTES)
    worker = threading.Thread(target=loop, args=(minutes * 60,), name="garmin-sync", daemon=True)
    worker.start()


def stop() -> None:
    global worker
    stop_event.set()
    if worker:
        worker.join(timeout=5)  # a sync waiting on Claude stays behind; the thread is a daemon
    worker = None
