"""Training metrics: pure arithmetic over the plan and what was done. Charts and every LLM prompt share them."""
import math
from dataclasses import dataclass
from datetime import date, timedelta

from app import clock, storage
from app.models import DayLoad, Match, Metrics, PlanFile, SavedRequest, Snapshot, TrendPoint, WeekVolume

DEFAULT_HR_MAX = 190
DEFAULT_HR_REST = 55
CTL_DAYS = 42
ATL_DAYS = 7
INTENSITY = {
    "recovery": 0.6, "easy": 0.75, "long": 0.85, "tempo": 1.1, "intervals": 1.3, "race": 1.4,
    "cross_training": 0.6, "strength": 0.5,
}
EASY_TYPES = {"easy", "long", "recovery", "unplanned"}
QUALITY_TYPES = {"tempo", "intervals", "race"}


@dataclass
class Entry:
    """One thing the athlete did, from a file or the logbook."""

    id: str
    date: date
    modality: str
    minutes: float
    km: float
    avg_hr: int
    match: Match


def entries(plan_id: str) -> list[Entry]:
    return [
        Entry(a.id, a.date, a.modality, a.moving_time_s / 60, a.distance_km, a.avg_hr, a.match)
        for a in storage.list_activities(plan_id)
    ]


def heart_rates(request: SavedRequest) -> tuple[float, float]:
    hr_max = request.hr_max or (208 - 0.7 * request.age if request.age else DEFAULT_HR_MAX)
    return hr_max, request.hr_rest or DEFAULT_HR_REST


def trimp(minutes: float, avg_hr: int, hr_max: float, hr_rest: float) -> float:
    """Banister TRIMP."""
    hrr = min(max((avg_hr - hr_rest) / (hr_max - hr_rest), 0), 1)
    return minutes * hrr * 0.64 * math.exp(1.92 * hrr)


def session_type(entry: Entry, plan_file: PlanFile) -> str:
    """The planned type, or for unplanned entries what its modality implies."""
    if entry.match.session_index >= 0:
        for week in plan_file.plan.weeks:
            if week.week_number == entry.match.week_number and entry.match.session_index < len(week.sessions):
                return week.sessions[entry.match.session_index].type
    return {"strength": "strength", "cross": "cross_training"}.get(entry.modality, "unplanned")


def entry_load(entry: Entry, kind: str, hr_max: float, hr_rest: float) -> float:
    if entry.avg_hr:
        return trimp(entry.minutes, entry.avg_hr, hr_max, hr_rest)
    return entry.minutes * INTENSITY.get(kind, INTENSITY["easy"])


def fitness_series(loads: list[float], seed: float) -> list[tuple[float, float, float]]:
    """(ctl, atl, tsb) per day. tsb uses yesterday's values, so today's load doesn't count against today's form."""
    ctl, atl = seed, 0.0
    series = []
    for load in loads:
        tsb = ctl - atl
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        series.append((ctl, atl, tsb))
    return series


def build_days(done: list[Entry], kinds: dict, plan_file: PlanFile, today: date) -> list[DayLoad]:
    # Runs imported from before the plan still count: real history beats the seed.
    first = min([plan_file.plan.weeks[0].start_date, *(e.date for e in done)])
    span = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    hr_max, hr_rest = heart_rates(plan_file.request)
    loads = {day: 0.0 for day in span}
    kms = {day: 0.0 for day in span}
    for entry in done:
        if entry.date in loads:
            loads[entry.date] += entry_load(entry, kinds[entry.id], hr_max, hr_rest)
            if entry.modality == "run":
                kms[entry.date] += entry.km
    series = fitness_series(list(loads.values()), plan_file.request.current_weekly_km * 1.1)
    return [
        DayLoad(date=day, load=round(loads[day], 1), ctl=round(ctl, 1), atl=round(atl, 1), tsb=round(tsb, 1), km=round(kms[day], 2))
        for day, (ctl, atl, tsb) in zip(span, series)
    ]


def in_week(entry: Entry, week) -> bool:
    if entry.match.session_index >= 0:
        return entry.match.week_number == week.week_number
    return week.start_date <= entry.date <= week.start_date + timedelta(days=6)


def build_weeks(done: list[Entry], plan_file: PlanFile) -> list[WeekVolume]:
    weeks = []
    for week in plan_file.plan.weeks:
        here = [entry for entry in done if in_week(entry, week)]
        matched = {e.match.session_index for e in here if 0 <= e.match.session_index < len(week.sessions)}
        strength = [i for i, s in enumerate(week.sessions) if s.modality == "strength"]
        unplanned_strength = sum(e.modality == "strength" and e.match.session_index < 0 for e in here)
        weeks.append(WeekVolume(
            week_number=week.week_number,
            start_date=week.start_date,
            planned_km=week.planned_km,
            actual_km=round(sum(e.km for e in here if e.modality == "run"), 2),
            planned_sessions=len(week.sessions),
            completed_sessions=len(matched),
            planned_strength=len(strength),
            completed_strength=len(matched & set(strength)) + unplanned_strength,
            planned_cross_min=sum(s.duration_min for s in week.sessions if s.modality == "cross"),
            actual_cross_min=round(sum(e.minutes for e in here if e.modality == "cross"), 1),
        ))
    return weeks


def trend_point(entry: Entry, kind: str) -> TrendPoint:
    seconds = entry.minutes * 60
    speed = entry.km * 1000 / seconds
    return TrendPoint(
        date=entry.date,
        activity_id=entry.id,
        type=kind,
        distance_km=entry.km,
        avg_pace_s=round(seconds / entry.km),
        avg_hr=entry.avg_hr,
        efficiency=round(speed * 60 / entry.avg_hr, 3) if entry.avg_hr else 0,
    )


def snapshot(days: list[DayLoad], easy: list[TrendPoint], seed: float, today: date) -> Snapshot:
    last = days[-1] if days else DayLoad(date=today, load=0, ctl=round(seed, 1), atl=0, tsb=round(seed, 1), km=0)
    week_ago = days[-8].ctl if len(days) >= 8 else round(seed, 1)
    since = today - timedelta(days=27)
    recent = [p for p in easy if since <= p.date <= today]
    with_hr = [p.avg_hr for p in recent if p.avg_hr]
    return Snapshot(
        ctl=last.ctl,
        atl=last.atl,
        tsb=last.tsb,
        ctl_7d_change=round(last.ctl - week_ago, 1),
        weekly_km_4wk_avg=round(sum(d.km for d in days if d.date >= since) / 4, 1),
        easy_pace_4wk_avg_s=round(sum(p.avg_pace_s for p in recent) / len(recent)) if recent else 0,
        easy_hr_4wk_avg=round(sum(with_hr) / len(with_hr)) if with_hr else 0,
    )


def compute(plan_file: PlanFile, today: date | None = None) -> Metrics:
    today = today or clock.today()
    done = entries(plan_file.meta.id)
    kinds = {entry.id: session_type(entry, plan_file) for entry in done}
    days = build_days(done, kinds, plan_file, today)
    runs = [e for e in done if e.modality == "run" and e.km > 0 and e.minutes > 0]
    # shortcut: unplanned runs count as easy so imported history has a trend, though some were surely hard; upgrade with a run classifier. TODO(v3.1)
    easy = [trend_point(e, kinds[e.id]) for e in runs if kinds[e.id] in EASY_TYPES]
    quality = [trend_point(e, kinds[e.id]) for e in runs if kinds[e.id] in QUALITY_TYPES]
    return Metrics(
        as_of=today,
        days=days,
        weeks=build_weeks(done, plan_file),
        easy_runs=easy,
        quality_runs=quality,
        current=snapshot(days, easy, plan_file.request.current_weekly_km * 1.1, today),
    )


def refresh(plan_file: PlanFile, today: date | None = None) -> Metrics:
    metrics = compute(plan_file, today)
    storage.write_json(storage.plan_dir(plan_file.meta.id) / "metrics_cache.json", metrics.model_dump_json(indent=2))
    return metrics


def for_prompt(plan_file: PlanFile) -> str:
    """The same numbers the charts show."""
    return refresh(plan_file).model_dump_json(indent=2)
