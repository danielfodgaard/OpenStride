"""The per-5-second series behind a workout's route and charts. Only `track` goes to the LLM, thinned to half a minute."""
from bisect import bisect_right

STEP_S = 5
PACE_WINDOW_S = 30
STOPPED_M_PER_S = 0.5
TRACK_STEP_S = 30
COLUMNS = ["km", "seconds", "pace_s_per_km", "hr", "lat", "lon", "altitude_m", "cadence"]
MAX_STRIDES_PER_MIN = 120


def build_series(activity_id: str, points: list, run: bool = True) -> dict | None:
    """`points` are the parser's records, distance from zero. None when there's nothing to chart.

    Each row is COLUMNS in order; cadence is steps per minute for a run, as recorded for anything else.
    """
    timed = sorted(points, key=lambda point: point.time)
    if len(timed) < 2:
        return None
    start = timed[0].time
    seconds = [(point.time - start).total_seconds() for point in timed]
    span = seconds[-1]
    if span <= 0:
        return None

    distances = []  # never falling, so the distance axis stays usable with noisy records
    for point in timed:
        distances.append(max(point.distance_km, distances[-1] if distances else 0))

    def km_at(t: float) -> float:
        i = bisect_right(seconds, t)
        if i >= len(seconds):
            return distances[-1]
        before, after = seconds[i - 1], seconds[i]
        share = (t - before) / (after - before) if after > before else 0
        return distances[i - 1] + share * (distances[i] - distances[i - 1])

    rows = []
    half = PACE_WINDOW_S / 2
    for step in range(int(span // STEP_S) + 1):
        t = step * STEP_S
        first, last = max(t - half, 0), min(t + half, span)
        speed = (km_at(last) - km_at(first)) * 1000 / (last - first)
        point = timed[bisect_right(seconds, t) - 1]
        cadence = point.cadence or None
        if cadence and run and cadence <= MAX_STRIDES_PER_MIN:
            cadence *= 2
        rows.append([
            round(km_at(t), 3),
            t,
            round(1000 / speed) if speed >= STOPPED_M_PER_S else None,
            point.hr or None,
            point.lat,
            point.lon,
            round(point.altitude, 1) if point.altitude is not None else None,
            cadence,
        ])
    return {"activity_id": activity_id, "step_s": STEP_S, "points": rows}


def track(series: dict | None, every_s: int = TRACK_STEP_S) -> dict | None:
    """The series without positions, one row per `every_s`, for a prompt. None when there is no series."""
    if not series:
        return None
    stride = max(every_s // series.get("step_s", STEP_S), 1)
    columns = ["km", "seconds", "pace_s_per_km", "hr", "altitude_m", "cadence"]
    rows = [[*row[:4], *row[6:8]] for row in series["points"][::stride]]
    return {"columns": columns, "rows": rows}
