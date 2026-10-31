"""Turns .fit/.gpx files into ActivitySummary facts and matches them to planned sessions."""
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import fitdecode
import gpxpy
from gpxpy.gpx import GPXException

from app import generator, lifecycle, review, storage
from app.models import ActivitySummary, Lap, Match, PlanFile

MOVING_KM_PER_S = 0.0005  # below 0.5 m/s the runner has stopped
ELEVATION_NOISE_M = 3  # ignores altimeter jitter when summing climbs


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


def parse_fit(content: bytes) -> Parsed:
    points, laps = [], []
    session, activity = {}, {}
    with fitdecode.FitReader(io.BytesIO(content)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            values = {f.name: f.value for f in frame.fields}
            if frame.name == "record" and values.get("timestamp"):
                distance = values.get("distance")
                points.append(Point(
                    time=values["timestamp"],
                    distance_km=distance / 1000 if distance is not None else (points[-1].distance_km if points else 0),
                    hr=values.get("heart_rate") or 0,
                    cadence=values.get("cadence") or 0,
                    altitude=values.get("enhanced_altitude", values.get("altitude")),
                ))
            elif frame.name == "lap":
                km = (values.get("total_distance") or 0) / 1000
                seconds = values.get("total_timer_time") or 0
                laps.append(Lap(
                    index=len(laps) + 1,
                    distance_km=round(km, 3),
                    time_s=round(seconds),
                    pace=format_pace(seconds, km),
                    avg_hr=values.get("avg_heart_rate") or 0,
                ))
            elif frame.name == "session":
                session = values
            elif frame.name == "activity":
                activity = values

    offset = None
    if activity.get("local_timestamp") and activity.get("timestamp"):
        # fitdecode tags the local wall clock as UTC, so the difference is the offset.
        offset = activity["local_timestamp"] - activity["timestamp"]
    distance = session.get("total_distance")
    return Parsed(
        points=points,
        laps=laps,
        utc_offset=offset,
        distance_km=distance / 1000 if distance is not None else None,
        moving_time_s=session.get("total_timer_time"),
        elapsed_time_s=session.get("total_elapsed_time"),
        avg_hr=session.get("avg_heart_rate"),
        max_hr=session.get("max_heart_rate"),
        avg_cadence=session.get("avg_running_cadence") or session.get("avg_cadence"),
        elevation_gain_m=session.get("total_ascent"),
    )


def parse_gpx(content: bytes) -> Parsed:
    points = []
    distance = 0.0
    for track in gpxpy.parse(content.decode("utf-8")).tracks:
        for segment in track.segments:
            previous = None
            for point in segment.points:
                if point.time is None:
                    continue
                if previous is not None:
                    distance += point.distance_2d(previous) / 1000
                previous = point
                extensions = {
                    node.tag.rsplit("}", 1)[-1]: node.text
                    for extension in point.extensions
                    for node in extension.iter()
                }
                points.append(Point(
                    time=point.time,
                    distance_km=distance,
                    hr=int(extensions.get("hr") or 0),
                    cadence=int(extensions.get("cad") or 0),
                    altitude=point.elevation,
                ))
    return Parsed(points=points)


def moving_seconds(points: list[Point]) -> list[float]:
    """Cumulative moving time at each point."""
    totals = [0.0]
    for previous, point in zip(points, points[1:]):
        seconds = (point.time - previous.time).total_seconds()
        km = point.distance_km - previous.distance_km
        moving = seconds > 0 and km / seconds >= MOVING_KM_PER_S
        totals.append(totals[-1] + (seconds if moving else 0))
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
    start = parsed.points[0].time
    if start.tzinfo is None:
        return start  # a GPX time with no zone is already local
    if parsed.utc_offset is not None:
        return (start + parsed.utc_offset).replace(tzinfo=None)
    # shortcut: files without their own offset use the server's zone, wrong for runs while travelling; upgrade with a per-activity zone. TODO(v2.1)
    return start.astimezone().replace(tzinfo=None)


def summarise(parsed: Parsed, source_file: str, plan_id: str, user_note: str, rpe: int) -> ActivitySummary:
    points = parsed.points
    if len(points) < 2:
        raise ValueError("no timed track points")
    moving = moving_seconds(points)
    distance_km = parsed.distance_km if parsed.distance_km is not None else points[-1].distance_km
    moving_s = parsed.moving_time_s if parsed.moving_time_s is not None else moving[-1]
    elapsed_s = parsed.elapsed_time_s if parsed.elapsed_time_s is not None else (points[-1].time - points[0].time).total_seconds()
    hrs = [point.hr for point in points if point.hr]
    cadences = [point.cadence for point in points if point.cadence]
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
        avg_cadence_spm=2 * (parsed.avg_cadence or (round(sum(cadences) / len(cadences)) if cadences else 0)),
        elevation_gain_m=parsed.elevation_gain_m if parsed.elevation_gain_m is not None else climbed(points),
        laps=parsed.laps,
        km_splits=km_splits(points, moving),
        user_note=user_note,
        rpe=rpe,
        match=Match(),
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


def sessions_on(plan_file: PlanFile, day) -> list:
    return [
        (week.week_number, index, session)
        for week in plan_file.plan.weeks
        for index, session in enumerate(week.sessions)
        if session.date == day
    ]


def match_activity(activity: ActivitySummary, plan_file: PlanFile) -> Match:
    found = closest_session(sessions_on(plan_file, activity.date), activity.distance_km)
    if found is None:
        return Match()
    return Match(planned_date=activity.date.isoformat(), week_number=found[0], session_index=found[1])


def import_files(uploads: list[tuple[str, bytes]], user_note: str, rpe: int) -> list[dict]:
    """One result per file; a bad file doesn't stop the others."""
    plan_file = lifecycle.active_plan()
    if plan_file is None:
        raise lifecycle.Conflict("There is no active plan to import runs into.")
    results = []
    for name, content in uploads:
        try:
            activity = parse_file(name, content, plan_file.meta.id, user_note, rpe)
        except (ValueError, IndexError, fitdecode.FitError, GPXException) as error:
            results.append({"file": name, "status": "error", "message": f"Could not read {name}: {error}"})
            continue
        if storage.activity_path(activity.id).exists():
            results.append({"file": name, "status": "duplicate", "message": f"{name} is already imported"})
            continue
        activity.match = match_activity(activity, plan_file)
        storage.save_activity(activity)
        result = {"file": name, "status": "imported", "activity": activity.model_dump(mode="json")}
        try:
            review.review_activity(activity)
        except generator.GenerationError as error:
            # The run is saved either way; the review can be re-run.
            result["message"] = f"{name}: imported, but the review failed: {error}"
        results.append(result)
    return results


def with_review(activity: ActivitySummary) -> dict:
    review_file = storage.load_review(activity.id)
    return activity.model_dump(mode="json") | {"review": review_file.model_dump(mode="json") if review_file else None}


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
            week = next((w for w in plan_file.plan.weeks if w.week_number == week_number), None)
            if week is None or not 0 <= index < len(week.sessions):
                raise lifecycle.Conflict("That planned session doesn't exist.")
            activity.match = Match(
                planned_date=week.sessions[index].date.isoformat(),
                week_number=week_number,
                session_index=index,
            )
    storage.save_activity(activity)
    return activity
