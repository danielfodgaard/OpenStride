"""Turns .fit/.gpx files into ActivitySummary facts and matches them to planned sessions."""
import io
import logging
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

import fitdecode
import gpxpy

from app import clock, lifecycle, metrics, review, storage, vdot
from app.models import ActivitySummary, Lap, Match, PlanFile
from app.series import build_series

MOVING_KM_PER_S = 0.0005  # below 0.5 m/s the runner has stopped
ELEVATION_NOISE_M = 3  # ignores altimeter jitter when summing climbs
EARTH_RADIUS_KM = 6371.0
SEMICIRCLE_DEGREES = 180 / 2**31
MAX_UTC_OFFSET = timedelta(hours=14)
MIN_SPLIT_COVERAGE = 0.9
EARLIEST_YEAR = 2000
MAX_RECORD_GAP = timedelta(days=2)
MAX_STRIDES_PER_MIN = 120  # above this a device is already reporting steps per minute
DUPLICATE_WINDOW = timedelta(seconds=60)  # the same run exported as .fit and .gpx starts a little apart
FUTURE_WARNING = "This run is dated after today. Check the watch clock or time zone."
log = logging.getLogger(__name__)
STRAVA_TYPES = {"1": "cycling", "4": "hiking", "9": "running", "10": "walking", "16": "swimming"}


EXTRA_VERSION = 1  # bump when `extra` gains fields, so stored files are read again at startup

# What the summary, laps and points already carry, plus bookkeeping and positions. Every other named field a session
# or lap message holds goes to `extra`, named and in the units the FIT profile gives it.
EXTRA_SKIP = {
    "timestamp", "start_time", "message_index", "event", "event_type", "event_group", "trigger", "first_lap_index",
    "num_laps", "sport", "total_distance", "total_timer_time", "total_elapsed_time", "avg_heart_rate", "max_heart_rate",
    "avg_running_cadence", "avg_cadence", "total_ascent", "avg_speed", "max_speed", "altitude", "avg_altitude",
    "max_altitude", "min_altitude", "speed", "wkt_step_index", "first_length_index", "start_position_lat",
    "start_position_long", "end_position_lat", "end_position_long", "nec_lat", "nec_long", "swc_lat", "swc_long",
}
# Record fields the points already hold, or that encode nothing a coach reads.
RECORD_SKIP = {
    "timestamp", "distance", "speed", "enhanced_speed", "heart_rate", "cadence", "fractional_cadence", "altitude",
    "enhanced_altitude", "position_lat", "position_long", "activity_type", "cadence256", "compressed_speed_distance",
    "time128", "device_index", "zone",
}
ZONE_FIELDS = (
    "time_in_hr_zone", "hr_zone_high_boundary", "time_in_speed_zone", "speed_zone_high_boundary", "time_in_cadence_zone",
    "cadence_zone_high_bondary", "time_in_power_zone", "power_zone_high_boundary", "max_heart_rate", "resting_heart_rate",
    "threshold_heart_rate", "functional_threshold_power",
)


@dataclass
class Point:
    time: datetime
    distance_km: float
    hr: int = 0
    cadence: int = 0  # strides per minute, as both formats record it
    altitude: float | None = None
    lat: float | None = None
    lon: float | None = None
    more: dict = field(default_factory=dict)  # the record's other numbers: running dynamics, temperature, power


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
    extra: dict = field(default_factory=dict)


def plain(value):
    """A FIT value as JSON: a number, text, or a tuple of numbers. Dates, bytes and the like are left out."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 3)
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, tuple) and value and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return [round(v, 3) if isinstance(v, float) else v for v in value]
    return None


def fit_extra(values: dict, skip: set) -> dict:
    """Every named field with a value, beyond `skip`. Garmin's unnamed fields have no known meaning, so they stay out."""
    out = {}
    for name, value in values.items():
        if name in skip or name.startswith("unknown_"):
            continue
        kept = plain(value)
        if kept is not None and kept != "":
            out[name] = kept
    return out


def with_pace(extra: dict, sport: str) -> dict:
    """A runner reads speed as pace: the fastest speed becomes a best pace, next to the m/s the file gives."""
    speed = extra.get("enhanced_max_speed")
    if sport == "running" and isinstance(speed, (int, float)) and speed > 0:
        extra["max_pace"] = format_pace(1000 / speed, 1)
    return extra


def format_pace(seconds: float, km: float) -> str:
    return vdot.format_pace(seconds / 60 / km) if km > 0 else ""


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


def read_fit_frames(content: bytes) -> tuple[list[dict], list[dict], list[dict], dict, dict, list[dict]]:
    records, laps, sessions, zones = [], [], [], []
    activity, file_id = {}, {}
    collect = {"record": records, "lap": laps, "session": sessions, "time_in_zone": zones}
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
    return records, laps, sessions, activity, file_id, zones


def session_zones(zones: list[dict]) -> dict:
    """The time-in-zone message about the whole session: seconds per zone, the zone boundaries, and the heart-rate
    settings the zones were built from, prefixed so they don't read as the run's own maximum."""
    whole = next((z for z in zones if str(z.get("reference_mesg")) == "session"), zones[-1] if zones else {})
    return {
        f"zones_{name}" if name.endswith("heart_rate") else name: plain(whole[name])
        for name in ZONE_FIELDS if plain(whole.get(name)) is not None
    }


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
        lat, lon = record.get("position_lat"), record.get("position_long")
        here = (lat * SEMICIRCLE_DEGREES, lon * SEMICIRCLE_DEGREES) if lat is not None and lon is not None else None
        if not has_distance and here:  # some devices only record GPS
            km += distance_between(*position, *here) if position else 0
            position = here
        points.append(Point(
            time=record["timestamp"],
            distance_km=km,
            hr=record.get("heart_rate") or 0,
            cadence=record.get("cadence") or 0,
            altitude=record.get("enhanced_altitude", record.get("altitude")),
            lat=round(here[0], 6) if here else None,
            lon=round(here[1], 6) if here else None,
            more={k: v for k, v in fit_extra(record, RECORD_SKIP).items() if isinstance(v, (int, float))},
        ))
    return points


def parse_fit(content: bytes) -> Parsed:
    records, lap_values, sessions, activity, file_id, zones = read_fit_frames(content)
    if file_id.get("type") not in (None, "activity"):
        raise ValueError(f"this is a {file_id['type']} file, not an activity")

    # shortcut: a multisport file counts as one activity of its longest sport; upgrade by splitting it. TODO(v3.1)
    main = max(sessions, key=lambda s: s.get("total_timer_time") or 0, default={})
    sport = fit_sport(main)
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
            extra=with_pace(fit_extra(values, EXTRA_SKIP | {"sub_sport"}), sport),
        ))

    offset = None
    if isinstance(activity.get("local_timestamp"), datetime) and isinstance(activity.get("timestamp"), datetime):
        # fitdecode tags the local wall clock as UTC, so the difference is the offset.
        offset = activity["local_timestamp"] - activity["timestamp"]
        if abs(offset) > MAX_UTC_OFFSET:  # some devices write a counter here, not a time
            offset = None

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
        sport=sport,
        start_time=start,
        distance_km=distance / 1000 if distance else None,
        moving_time_s=total("total_timer_time"),
        elapsed_time_s=total("total_elapsed_time"),
        avg_hr=main.get("avg_heart_rate"),
        max_hr=main.get("max_heart_rate"),
        avg_cadence=main.get("avg_running_cadence") or main.get("avg_cadence"),
        elevation_gain_m=total("total_ascent"),
        extra=with_pace(fit_extra(main, EXTRA_SKIP) | session_zones(zones), sport),
    )


def number(text: str | None) -> int:
    try:
        return round(float(text or 0))
    except ValueError:
        return 0


GPX_KNOWN = {"hr", "heartrate", "cad", "cadence", "trackpointextension", "trackpointextensionv1", "trackpointextensionv2"}


def gpx_more(extensions: dict) -> dict:
    """The other numeric extensions a point carries (atemp, power, ...), named as the file names them."""
    out = {}
    for name, text in extensions.items():
        if name in GPX_KNOWN or text is None:
            continue
        try:
            out[name] = float(text.strip())
        except ValueError:
            continue
    return out


def gpx_sport(gpx) -> str:
    """Running unless <type> says otherwise. Strava writes numbers there."""
    text = next((track.type for track in gpx.tracks if track.type), None)
    if text is None:
        return "running"
    kind = STRAVA_TYPES.get(text.strip(), text.strip().lower())
    return "running" if any(word in kind for word in ("run", "jog", "treadmill")) else kind


def parse_gpx(content: bytes) -> Parsed:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:  # older exporters declare ISO-8859-1
        text = content.decode("latin-1")
    gpx = gpxpy.parse(text)
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
                    lat=point.latitude,
                    lon=point.longitude,
                    more=gpx_more(extensions),
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


def points_extra(points: list[Point], run: bool) -> dict:
    """What a stretch of records says beyond pace and average heart rate: its maximum, cadence, climb, and the
    average of every other number the records carry (running dynamics, temperature, power)."""
    out = {}
    hrs = [p.hr for p in points if p.hr]
    if hrs:
        out["max_hr"] = max(hrs)
    cadences = [p.cadence for p in points if p.cadence]
    if cadences:
        mean = round(sum(cadences) / len(cadences))
        out["avg_cadence_spm" if run else "avg_cadence"] = steps_per_minute(mean) if run else mean
    altitudes = [p.altitude for p in points if p.altitude is not None]
    if len(altitudes) > 1:
        out["ascent_m"] = climbed(points)
        out["descent_m"] = climbed([replace(p, altitude=-p.altitude) for p in points if p.altitude is not None])
    names = {name for p in points for name in p.more}
    for name in sorted(names):
        values = [p.more[name] for p in points if name in p.more]
        out[f"avg_{name}"] = round(sum(values) / len(values), 3)
    return out


def km_splits(points: list[Point], moving: list[float], run: bool = True) -> list[Lap]:
    splits = []
    split_start_km = split_start_s = 0.0
    split_points = []

    def close(km: float, seconds: float) -> None:
        nonlocal split_start_km, split_start_s, split_points
        distance = km - split_start_km
        hrs = [p.hr for p in split_points if p.hr]
        splits.append(Lap(
            index=len(splits) + 1,
            distance_km=round(distance, 3),
            time_s=round(seconds - split_start_s),
            pace=format_pace(seconds - split_start_s, distance),
            avg_hr=round(sum(hrs) / len(hrs)) if hrs else 0,
            extra=points_extra(split_points, run),
        ))
        split_start_km, split_start_s, split_points = km, seconds, []

    for i in range(1, len(points)):
        before, after = points[i - 1], points[i]
        while after.distance_km >= len(splits) + 1:
            target = len(splits) + 1.0
            # Interpolate the moment the km boundary was crossed.
            share = (target - before.distance_km) / (after.distance_km - before.distance_km)
            close(target, moving[i - 1] + share * (moving[i] - moving[i - 1]))
        split_points.append(after)
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


def utc_start(parsed: Parsed) -> str:
    start = parsed.start_time or parsed.points[0].time
    if start.tzinfo is None:
        start = start.astimezone()
    return start.astimezone(timezone.utc).isoformat(timespec="seconds")


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
    if local_start(parsed).year < EARLIEST_YEAR:
        raise ValueError(f"its timestamps look wrong (dated {local_start(parsed).date()})")
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
        km_splits=km_splits(points, moving, modality == "run") if covered else [],
        user_note=user_note,
        rpe=rpe,
        match=Match(),
        modality=modality,
        sport=parsed.sport,
        start_utc=utc_start(parsed),
        extra=parsed.extra,
        extra_version=EXTRA_VERSION,
    )


def parse_bytes(name: str, content: bytes) -> Parsed:
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix == "fit":
        return parse_fit(content)
    if suffix == "gpx":
        return parse_gpx(content)
    raise ValueError("only .fit and .gpx files are supported")


def parse_file(name: str, content: bytes, plan_id: str, user_note: str = "", rpe: int = 0) -> ActivitySummary:
    return summarise(parse_bytes(name, content), name, plan_id, user_note, rpe)


def store_series(activity: ActivitySummary, parsed: Parsed) -> None:
    """The series, and the UTC start time that older imports didn't record."""
    series = build_series(activity.id, rebased(parsed.points), run=activity.modality == "run")
    if series:
        storage.save_series(series)
    if not activity.start_utc:
        activity.start_utc = utc_start(parsed)
        storage.save_activity(activity)


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
        if session.date == day and session.modality == modality and session.status != "skipped"
    ]


def match_activity(activity, plan_file: PlanFile, day: date | None = None) -> Match:
    """The planned session of the same kind on `day` (the workout's date unless given) nearest in distance.

    `activity` is an ActivitySummary or a ManualActivity: both have a date, modality and distance.
    """
    day = day or activity.date
    found = closest_session(sessions_on(plan_file, day, activity.modality), activity.distance_km)
    if found is None:
        return Match()
    return Match(planned_date=day.isoformat(), week_number=found[0], session_index=found[1])


def date_warnings(activity: ActivitySummary) -> list[str]:
    return [FUTURE_WARNING] if activity.start_time > clock.now() else []


def find_duplicate(activity: ActivitySummary) -> ActivitySummary | None:
    """Per plan, so a run can be imported again under a new plan."""
    for other in storage.list_activities(activity.plan_id):
        if other.modality == activity.modality and abs(other.start_time - activity.start_time) <= DUPLICATE_WINDOW:
            return other
    return None


def import_one(name: str, content: bytes, plan_file: PlanFile | None, user_note: str, rpe: int, external_id: str = "",
               parsed: Parsed | None = None) -> dict:
    """`parsed` given means a summary from a service with no file behind it: saved as partial, with no raw or series."""
    plan_id = plan_file.meta.id if plan_file else ""
    partial = parsed is not None
    try:
        parsed = parsed or parse_bytes(name, content)
        activity = summarise(parsed, name, plan_id, user_note, rpe)
    except Exception as error:  # third-party parsers on arbitrary files; one bad file must not stop the rest
        return {"file": name, "status": "error", "message": f"Could not read {name}: {error}"}
    with storage.workout_lock(plan_id):
        plan_file = storage.load_plan(plan_id) if plan_id else None  # may have moved on since the last file
        existing = find_duplicate(activity)
        if existing and (partial or storage.find_raw(existing.id)):
            return {"file": name, "status": "duplicate", "message": f"{name} is already imported"}
        if existing:  # imported before v3.1: keep the workout, add what its detail page needs
            storage.save_raw(existing.id, name, content)
            store_series(existing, parsed)
            return {"file": name, "status": "updated", "message": f"{name}: route and charts added"}
        activity.id = storage.new_activity_id(activity.id)
        activity.external_id = external_id
        activity.partial = partial
        if plan_file:
            activity.match = match_activity(activity, plan_file)
        storage.save_activity(activity)
        if not partial:
            storage.save_raw(activity.id, name, content)
            store_series(activity, parsed)
        if plan_file:
            metrics.refresh(plan_file)
    return {
        "file": name,
        "status": "imported",
        "activity": activity.model_dump(mode="json"),
        "warnings": date_warnings(activity),
        "summary": activity,
    }


def add_review(result: dict) -> dict:
    activity = result.pop("summary", None)
    if activity:
        try:
            review.review_activity(activity)
        except Exception as error:  # a network or plan-state failure too: the run must survive its review
            # The run is saved either way; the review can be re-run.
            result["message"] = f"{result['file']}: imported, but the review failed: {error}"
    return result


def import_files(uploads: list[tuple[str, bytes]], user_note: str, rpe: int) -> list[dict]:
    """One result per file; a bad file doesn't stop the others."""
    plan_file = lifecycle.active_plan()
    # All saved before any review, or a review sees the rest of the batch as missed sessions.
    return [add_review(result) for result in [import_one(name, content, plan_file, user_note, rpe) for name, content in uploads]]


def import_fit_bytes(content: bytes, source: str, external_id: str) -> dict:
    """A .fit that arrived from a service: same steps as an upload, once per `external_id`."""
    # shortcut: the file name shows as the title of an unplanned workout; TODO(v4.1) use the service's activity name.
    name = f"{source}_{external_id.rsplit(':', 1)[-1]}.fit"
    if any(activity.external_id == external_id for activity in storage.list_all_activities()):
        return {"file": name, "status": "duplicate", "message": f"{name} is already imported"}
    return import_one(name, content, lifecycle.active_plan(), "", 0, external_id)  # the caller adds the review


def plan_entries(plan_id: str) -> list:
    """Every workout on a plan, each with the function that saves it: files and hand-logged alike."""
    return [
        *((a, storage.save_activity) for a in storage.list_activities(plan_id)),
        *((m, storage.save_manual) for m in storage.list_manual(plan_id)),
    ]


def rematch(plan_file: PlanFile) -> None:
    """After a new plan version, point matches at the same planned day. Keeps manual matches."""
    with storage.activities_lock():
        for entry, save in plan_entries(plan_file.meta.id):
            if not entry.match.planned_date:
                continue
            match = match_activity(entry, plan_file, date.fromisoformat(entry.match.planned_date))
            if match != entry.match:
                entry.match = match
                save(entry)


def with_review(activity: ActivitySummary, full: bool = True) -> dict:
    """`full` is the API's view. A prompt that lists every run on a plan gets the laps and splits without their
    per-lap extras; the coach's workout_details tool has the whole file for one run."""
    review_file = storage.load_review(activity.id)
    exclude = None if full else {"laps": {"__all__": {"extra"}}, "km_splits": {"__all__": {"extra"}}}
    return activity.model_dump(mode="json", exclude=exclude) | {
        "review": review_file.model_dump(mode="json") if review_file else None,
        "warnings": date_warnings(activity),
    }


def refresh_extras() -> int:
    """Reads the stored file of every activity imported before `extra` existed, or before it last grew.

    Runs at startup; nothing here reaches the LLM. A file that fails to parse is left as it was and not retried.
    """
    count = 0
    for activity in storage.list_all_activities():
        if activity.extra_version >= EXTRA_VERSION:
            continue
        raw = storage.find_raw(activity.id)
        try:
            if raw is not None:
                parsed = parse_bytes(raw.name, raw.read_bytes())
                fresh = summarise(parsed, activity.source_file, activity.plan_id, activity.user_note, activity.rpe)
                activity.laps, activity.km_splits, activity.extra = fresh.laps, fresh.km_splits, fresh.extra
                store_series(activity, parsed)  # the series gained columns too
                count += 1
        except Exception as error:  # a file that parsed once may still trip on a field; the run stays as it was
            log.warning("Could not re-read %s for its extra fields: %s", activity.id, error)
        activity.extra_version = EXTRA_VERSION
        storage.save_activity(activity)
    return count


def plan_for_edit(plan_id: str) -> PlanFile | None:
    """A workout's own plan must be active; one with no plan can match the active plan, if any."""
    return lifecycle.require_active(plan_id) if plan_id else lifecycle.active_plan()


def planned_match(plan_file: PlanFile | None, week_number: int, index: int, modality: str) -> Match:
    """A match the user chose by hand. It has to point at a real session of the same kind."""
    if plan_file is None:
        raise lifecycle.Conflict("There is no active plan to match this to.")
    session = plan_file.session_at(week_number, index)
    if session is None:
        raise lifecycle.Conflict("That planned session doesn't exist.")
    if session.modality != modality:
        raise lifecycle.Conflict(f"A {modality} session can only match a planned {modality} session.")
    return Match(planned_date=session.date.isoformat(), week_number=week_number, session_index=index)


def edit(activity_id: str, user_note: str | None, rpe: int | None, match: tuple[int, int] | None) -> ActivitySummary:
    plan_file = plan_for_edit(storage.load_activity(activity_id).plan_id)
    with storage.workout_lock(plan_file.meta.id if plan_file else ""):
        activity = storage.load_activity(activity_id)
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
                activity.plan_id = plan_file.meta.id  # an unattached workout joins the plan it matches
        storage.save_activity(activity)
        if plan_file:
            metrics.refresh(plan_file)
        return activity


def delete(activity_id: str) -> None:
    plan_id = storage.load_activity(activity_id).plan_id
    plan_file = storage.load_plan(plan_id) if plan_id else None  # its plan may no longer exist
    with storage.workout_lock(plan_file.meta.id if plan_file else ""):
        storage.load_activity(activity_id)  # another device may have got here first
        storage.delete_activity(activity_id)
        if plan_file:
            metrics.refresh(plan_file)
