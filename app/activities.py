"""Turns .fit/.gpx files into ActivitySummary facts and matches them to planned sessions."""
import io
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

import fitdecode
import gpxpy

from app import generator, lifecycle, metrics, review, storage
from app.models import ActivitySummary, Lap, Match, PlanFile

MOVING_KM_PER_S = 0.0005  # below 0.5 m/s the runner has stopped
ELEVATION_NOISE_M = 3  # ignores altimeter jitter when summing climbs
EARTH_RADIUS_KM = 6371.0
SEMICIRCLE_DEGREES = 180 / 2**31
MAX_UTC_OFFSET = timedelta(hours=14)
MIN_SPLIT_COVERAGE = 0.9
MAX_RECORD_GAP = timedelta(days=2)
MAX_STRIDES_PER_MIN = 120  # above this a device is already reporting steps per minute
DUPLICATE_WINDOW = timedelta(seconds=60)  # the same run exported as .fit and .gpx starts a little apart
STRAVA_TYPES = {"1": "cycling", "4": "hiking", "9": "running", "10": "walking", "16": "swimming"}


@dataclass
class Point:
    time: datetime
    distance_km: float
    hr: int = 0
    cadence: int = 0  # strides per minute, as both formats record it
    altitude: float | None = None


@dataclass
class Parsed:
    """What a file says directly. Totals stay None when only the points can tell us."""

    points: list[Point]
    laps: list[Lap] = field(default_factory=list)
    sport: str = "running"
    start_time: datetime | None = None
    utc_offset: timedelta | None = None
    distance_km: float | None = None
    moving_time_s: float | None = None
    elapsed_time_s: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_cadence: int | None = None
    elevation_gain_m: float | None = None


def format_pace(seconds: float, km: float) -> str:
    if km <= 0:
        return ""
    total = round(seconds / km)
    return f"{total // 60}:{total % 60:02d}/km"


def distance_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Kilometres, by haversine."""
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + (
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def fit_sport(session: dict) -> str:
    sport = str(session.get("sport") or "running")
    if str(session.get("sub_sport")) == "strength_training":
        return "strength_training"
    return "running" if sport == "generic" else sport


def read_fit_frames(content: bytes) -> tuple[list[dict], list[dict], list[dict], dict, dict]:
    records, laps, sessions = [], [], []
    activity, file_id = {}, {}
    collect = {"record": records, "lap": laps, "session": sessions}
    try:
        # Watches do write files with bad checksums, so don't refuse them for that.
        with fitdecode.FitReader(io.BytesIO(content), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                values = {f.name: f.value for f in frame.fields}
                if frame.name in collect:
                    collect[frame.name].append(values)
                elif frame.name == "activity":
                    activity = values
                elif frame.name == "file_id":
                    file_id = values
    except fitdecode.FitError:
        if not records and not sessions:  # a watch that died mid-run still left the run
            raise
    return records, laps, sessions, activity, file_id


def fit_points(records: list[dict]) -> list[Point]:
    points = []
    km = 0.0
    position = None
    has_distance = any(r.get("distance") is not None for r in records)
    for record in records:
        if not isinstance(record.get("timestamp"), datetime):
            continue
        if has_distance:
            km = record["distance"] / 1000 if record.get("distance") is not None else km
        else:  # some devices only record GPS
            lat, lon = record.get("position_lat"), record.get("position_long")
            if lat is not None and lon is not None:
                here = (lat * SEMICIRCLE_DEGREES, lon * SEMICIRCLE_DEGREES)
                km += distance_between(*position, *here) if position else 0
                position = here
        points.append(Point(
            time=record["timestamp"],
            distance_km=km,
            hr=record.get("heart_rate") or 0,
            cadence=record.get("cadence") or 0,
            altitude=record.get("enhanced_altitude", record.get("altitude")),
        ))
    return points


def parse_fit(content: bytes) -> Parsed:
    records, lap_values, sessions, activity, file_id = read_fit_frames(content)
    if file_id.get("type") not in (None, "activity"):
        raise ValueError(f"this is a {file_id['type']} file, not an activity")

    laps = []
    for values in lap_values:
        km = (values.get("total_distance") or 0) / 1000
        seconds = values.get("total_timer_time") or 0
        laps.append(Lap(
            index=len(laps) + 1,
            distance_km=round(km, 3),
            time_s=round(seconds),
            pace=format_pace(seconds, km),
            avg_hr=values.get("avg_heart_rate") or 0,
        ))

    offset = None
    if isinstance(activity.get("local_timestamp"), datetime) and isinstance(activity.get("timestamp"), datetime):
        # fitdecode tags the local wall clock as UTC, so the difference is the offset.
        offset = activity["local_timestamp"] - activity["timestamp"]
        if abs(offset) > MAX_UTC_OFFSET:  # some devices write a counter here, not a time
            offset = None

    # shortcut: a multisport file counts as one activity of its longest sport; upgrade by splitting it. TODO(v3.1)
    main = max(sessions, key=lambda s: s.get("total_timer_time") or 0, default={})
    total = lambda key: sum(s.get(key) or 0 for s in sessions) or None
    distance = total("total_distance")
    start = main.get("start_time") if isinstance(main.get("start_time"), datetime) else None
    points = fit_points(records)
    if start:  # one Strava Android build wrote some records dated decades ahead
        points = [point for point in points if start - MAX_RECORD_GAP <= point.time <= start + MAX_RECORD_GAP]
    return Parsed(
        points=points,
        laps=laps,
        utc_offset=offset,
        sport=fit_sport(main),
        start_time=start,
        distance_km=distance / 1000 if distance else None,
        moving_time_s=total("total_timer_time"),
        elapsed_time_s=total("total_elapsed_time"),
        avg_hr=main.get("avg_heart_rate"),
        max_hr=main.get("max_heart_rate"),
        avg_cadence=main.get("avg_running_cadence") or main.get("avg_cadence"),
        elevation_gain_m=total("total_ascent"),
    )


def number(text: str | None) -> int:
    try:
        return round(float(text or 0))
    except ValueError:
        return 0


def gpx_sport(gpx) -> str:
    """Running unless <type> says otherwise. Strava writes numbers there."""
    text = next((track.type for track in gpx.tracks if track.type), None)
    if text is None:
        return "running"
    kind = STRAVA_TYPES.get(text.strip(), text.strip().lower())
    return "running" if any(word in kind for word in ("run", "jog", "treadmill")) else kind


def parse_gpx(content: bytes) -> Parsed:
    gpx = gpxpy.parse(content.decode("utf-8-sig"))
    points = []
    distance = 0.0
    for track in gpx.tracks:
        for segment in track.segments:
            previous = None
            for point in segment.points:
                if point.time is None:
                    continue
                if previous is not None:
                    distance += point.distance_2d(previous) / 1000
                previous = point
                extensions = {
                    node.tag.rsplit("}", 1)[-1].lower(): node.text
                    for extension in point.extensions
                    for node in extension.iter()
                }
                points.append(Point(
                    time=point.time,
                    distance_km=distance,
                    hr=number(extensions.get("hr") or extensions.get("heartrate")),
                    cadence=number(extensions.get("cad") or extensions.get("cadence")),
                    altitude=point.elevation,
                ))
    return Parsed(points=points, sport=gpx_sport(gpx))


def moving_seconds(points: list[Point], timer_s: float | None = None) -> list[float]:
    """Cumulative moving time at each point.

    With a device timer, only as much stopped time as the watch paused comes out: a runner
    standing at a light with the timer running is still on the clock, as in the watch's laps.
    """
    steps = [max((b.time - a.time).total_seconds(), 0) for a, b in zip(points, points[1:])]
    stopped = [
        i for i, (a, b) in enumerate(zip(points, points[1:]))
        if steps[i] and (b.distance_km - a.distance_km) / steps[i] < MOVING_KM_PER_S
    ]
    if timer_s is not None:
        paused = sum(steps) - timer_s
        longest_first, stopped = sorted(stopped, key=lambda i: -steps[i]), []
        for i in longest_first:
            if paused <= 0:
                break
            stopped.append(i)
            paused -= steps[i]
    excluded = set(stopped)
    totals = [0.0]
    for i, seconds in enumerate(steps):
        totals.append(totals[-1] + (0 if i in excluded else seconds))
    return totals


def km_splits(points: list[Point], moving: list[float]) -> list[Lap]:
    splits = []
    split_start_km = split_start_s = 0.0
    hrs = []

    def close(km: float, seconds: float) -> None:
        nonlocal split_start_km, split_start_s, hrs
        distance = km - split_start_km
        splits.append(Lap(
            index=len(splits) + 1,
            distance_km=round(distance, 3),
            time_s=round(seconds - split_start_s),
            pace=format_pace(seconds - split_start_s, distance),
            avg_hr=round(sum(hrs) / len(hrs)) if hrs else 0,
        ))
        split_start_km, split_start_s, hrs = km, seconds, []

    for i in range(1, len(points)):
        before, after = points[i - 1], points[i]
        while after.distance_km >= len(splits) + 1:
            target = len(splits) + 1.0
            # Interpolate the moment the km boundary was crossed.
            share = (target - before.distance_km) / (after.distance_km - before.distance_km)
            close(target, moving[i - 1] + share * (moving[i] - moving[i - 1]))
        if after.hr:
            hrs.append(after.hr)
    if points[-1].distance_km - split_start_km >= 0.05:  # drop GPS crumbs at the end
        close(points[-1].distance_km, moving[-1])
    return splits


def climbed(points: list[Point]) -> float:
    gain = 0.0
    reference = None
    for point in points:
        if point.altitude is None:
            continue
        if reference is None or point.altitude < reference:
            reference = point.altitude
        elif point.altitude - reference >= ELEVATION_NOISE_M:
            gain += point.altitude - reference
            reference = point.altitude
    return round(gain, 1)


def local_start(parsed: Parsed) -> datetime:
    start = parsed.start_time or parsed.points[0].time
    if start.tzinfo is None:
        return start  # a GPX time with no zone is already local
    if parsed.utc_offset is not None:
        return (start + parsed.utc_offset).replace(tzinfo=None)
    # shortcut: files without their own offset use the server's zone, wrong for runs while travelling; upgrade with a per-activity zone. TODO(v2.1)
    return start.astimezone().replace(tzinfo=None)


def rebased(points: list[Point]) -> list[Point]:
    """Distance from the start of the file, since a device may start counting anywhere."""
    if not points or not points[0].distance_km:
        return points
    offset = points[0].distance_km
    return [replace(point, distance_km=point.distance_km - offset) for point in points]


def steps_per_minute(cadence: int) -> int:
    return cadence if cadence > MAX_STRIDES_PER_MIN else 2 * cadence


def modality_of(sport: str) -> str:
    if sport == "strength_training":
        return "strength"
    return "run" if sport == "running" else "cross"


def summarise(parsed: Parsed, source_file: str, plan_id: str, user_note: str, rpe: int) -> ActivitySummary:
    if parsed.distance_km is None and parsed.moving_time_s is None and len(parsed.points) < 2:
        raise ValueError("no timed track points")
    if parsed.start_time is None and not parsed.points:
        raise ValueError("no start time")
    points = rebased(parsed.points)
    moving = moving_seconds(points, parsed.moving_time_s)
    hrs = [point.hr for point in points if point.hr]
    cadences = [point.cadence for point in points if point.cadence]
    distance_km = parsed.distance_km if parsed.distance_km is not None else (points[-1].distance_km if points else 0)
    moving_s = parsed.moving_time_s if parsed.moving_time_s is not None else moving[-1]
    elapsed_s = parsed.elapsed_time_s
    if elapsed_s is None:
        elapsed_s = (points[-1].time - points[0].time).total_seconds() if points else moving_s
    elapsed_s = max(elapsed_s, moving_s)  # files with out-of-order times can suggest otherwise
    modality = modality_of(parsed.sport)
    cadence = parsed.avg_cadence or (round(sum(cadences) / len(cadences)) if cadences else 0)
    # Records covering only part of the distance would give splits that don't add up.
    covered = bool(points) and points[-1].distance_km >= MIN_SPLIT_COVERAGE * distance_km
    start = local_start(parsed)
    return ActivitySummary(
        id=f"{start:%Y-%m-%dT%H-%M-%S}",
        plan_id=plan_id,
        source_file=source_file,
        start_time=start,
        date=start.date(),
        distance_km=round(distance_km, 3),
        moving_time_s=round(moving_s),
        elapsed_time_s=round(elapsed_s),
        avg_pace=format_pace(moving_s, distance_km),
        avg_hr=parsed.avg_hr or (round(sum(hrs) / len(hrs)) if hrs else 0),
        max_hr=parsed.max_hr or max(hrs, default=0),
        avg_cadence_spm=steps_per_minute(cadence) if modality == "run" else cadence,  # bike rpm isn't strides
        elevation_gain_m=parsed.elevation_gain_m if parsed.elevation_gain_m is not None else climbed(points),
        laps=parsed.laps,
        km_splits=km_splits(points, moving) if covered else [],
        user_note=user_note,
        rpe=rpe,
        match=Match(),
        modality=modality,
        sport=parsed.sport,
    )


def parse_file(name: str, content: bytes, plan_id: str, user_note: str = "", rpe: int = 0) -> ActivitySummary:
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix == "fit":
        parsed = parse_fit(content)
    elif suffix == "gpx":
        parsed = parse_gpx(content)
    else:
        raise ValueError("only .fit and .gpx files are supported")
    return summarise(parsed, name, plan_id, user_note, rpe)


def closest_session(sessions, distance_km: float) -> tuple[int, int] | None:
    """`sessions` is (week_number, session_index, session). The nearest planned distance wins."""
    if not sessions:
        return None
    week_number, index, _ = min(sessions, key=lambda item: abs(item[2].distance_km - distance_km))
    return week_number, index


def sessions_on(plan_file: PlanFile, day, modality: str) -> list:
    return [
        (week.week_number, index, session)
        for week in plan_file.plan.weeks
        for index, session in enumerate(week.sessions)
        if session.date == day and session.modality == modality
    ]


def match_activity(activity: ActivitySummary, plan_file: PlanFile) -> Match:
    found = closest_session(sessions_on(plan_file, activity.date, activity.modality), activity.distance_km)
    if found is None:
        return Match()
    return Match(planned_date=activity.date.isoformat(), week_number=found[0], session_index=found[1])


def is_duplicate(activity: ActivitySummary) -> bool:
    return any(
        abs(other.start_time - activity.start_time) <= DUPLICATE_WINDOW
        for other in storage.list_all_activities()
    )


def import_files(uploads: list[tuple[str, bytes]], user_note: str, rpe: int) -> list[dict]:
    """One result per file; a bad file doesn't stop the others."""
    plan_file = lifecycle.active_plan()
    if plan_file is None:
        raise lifecycle.Conflict("There is no active plan to import runs into.")
    results = []
    for name, content in uploads:
        try:
            activity = parse_file(name, content, plan_file.meta.id, user_note, rpe)
        except Exception as error:  # third-party parsers on arbitrary files; one bad file must not stop the rest
            results.append({"file": name, "status": "error", "message": f"Could not read {name}: {error}"})
            continue
        if is_duplicate(activity):
            results.append({"file": name, "status": "duplicate", "message": f"{name} is already imported"})
            continue
        activity.match = match_activity(activity, plan_file)
        storage.save_activity(activity)
        metrics.refresh(plan_file)
        result = {"file": name, "status": "imported", "activity": activity.model_dump(mode="json")}
        try:
            review.review_activity(activity)
        except generator.GenerationError as error:
            # The run is saved either way; the review can be re-run.
            result["message"] = f"{name}: imported, but the review failed: {error}"
        results.append(result)
    return results


def rematch(plan_file: PlanFile) -> None:
    """After a new plan version, point matches at the same planned day. Keeps manual matches."""
    saves = [
        *((a, storage.save_activity) for a in storage.list_activities(plan_file.meta.id)),
        *((m, storage.save_manual) for m in storage.list_manual(plan_file.meta.id)),
    ]
    for entry, save in saves:
        if not entry.match.planned_date:
            continue
        day = date.fromisoformat(entry.match.planned_date)
        found = closest_session(sessions_on(plan_file, day, entry.modality), entry.distance_km)
        match = Match(planned_date=entry.match.planned_date, week_number=found[0], session_index=found[1]) if found else Match()
        if match != entry.match:
            entry.match = match
            save(entry)


def with_review(activity: ActivitySummary) -> dict:
    review_file = storage.load_review(activity.id)
    return activity.model_dump(mode="json") | {"review": review_file.model_dump(mode="json") if review_file else None}


def planned_match(plan_file: PlanFile, week_number: int, index: int, modality: str) -> Match:
    """A match the user chose by hand. It has to point at a real session of the same kind."""
    week = next((w for w in plan_file.plan.weeks if w.week_number == week_number), None)
    if week is None or not 0 <= index < len(week.sessions):
        raise lifecycle.Conflict("That planned session doesn't exist.")
    if week.sessions[index].modality != modality:
        raise lifecycle.Conflict(f"A {modality} session can only match a planned {modality} session.")
    return Match(planned_date=week.sessions[index].date.isoformat(), week_number=week_number, session_index=index)


def edit(activity_id: str, user_note: str | None, rpe: int | None, match: tuple[int, int] | None) -> ActivitySummary:
    activity = storage.load_activity(activity_id)
    plan_file = lifecycle.require_active(activity.plan_id)
    if user_note is not None:
        activity.user_note = user_note
    if rpe is not None:
        activity.rpe = rpe
    if match is not None:
        week_number, index = match
        if index == -1:
            activity.match = Match()
        else:
            activity.match = planned_match(plan_file, week_number, index, activity.modality)
    storage.save_activity(activity)
    metrics.refresh(plan_file)
    return activity
