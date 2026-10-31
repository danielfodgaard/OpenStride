"""Daniels–Gilbert VDOT and training paces. Times are minutes, speeds metres per minute."""
import math

EASY_FRACTIONS = (0.59, 0.74)
MARATHON_FRACTION = (0.75 + 0.84) / 2
THRESHOLD_FRACTION = (0.83 + 0.88) / 2
INTERVAL_FRACTION = (0.95 + 1.00) / 2
REPETITION_FRACTION = 1.05


def parse_time(text: str) -> float:
    """Parse `h:mm:ss` or `mm:ss` into minutes. Raises ValueError on anything else."""
    parts = text.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isascii() and p.isdigit() for p in parts):
        raise ValueError(f"time must be h:mm:ss or mm:ss, got {text!r}")
    numbers = [int(p) for p in parts]
    if numbers[-1] > 59 or (len(numbers) == 3 and numbers[1] > 59):
        raise ValueError(f"minutes and seconds must be below 60, got {text!r}")
    hours = numbers[0] if len(numbers) == 3 else 0
    minutes = hours * 60 + numbers[-2] + numbers[-1] / 60
    if minutes <= 0:
        raise ValueError(f"time must be greater than zero, got {text!r}")
    return minutes


def vo2(v: float) -> float:
    return -4.60 + 0.182258 * v + 0.000104 * v**2


def percent_max(t: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)


def vdot(distance_km: float, t: float) -> float:
    return vo2(distance_km * 1000 / t) / percent_max(t)


def speed_for_vo2(target: float) -> float:
    a, b, c = 0.000104, 0.182258, -4.60 - target
    return (-b + math.sqrt(b**2 - 4 * a * c)) / (2 * a)


def predict_time(distance_km: float, target_vdot: float) -> float:
    """Minutes to race distance_km at target_vdot. vdot() falls as t rises, so bisect."""
    low, high = 0.1, 100_000.0
    for _ in range(100):
        mid = (low + high) / 2
        if vdot(distance_km, mid) > target_vdot:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def format_pace(min_per_km: float) -> str:
    seconds = round(min_per_km * 60)
    return f"{seconds // 60}:{seconds % 60:02d}/km"


def pace_for_fraction(current_vdot: float, fraction: float) -> str:
    return format_pace(1000 / speed_for_vo2(fraction * current_vdot))


def compute_paces(
    recent_distance_km: float,
    recent_time: str,
    race_distance_km: float,
    goal_time: str | None,
) -> dict:
    """Build the `paces` block of a plan file."""
    current = vdot(recent_distance_km, parse_time(recent_time))
    if goal_time:
        goal_minutes = parse_time(goal_time)
        goal = round(vdot(race_distance_km, goal_minutes), 1)
    else:
        goal_minutes = predict_time(race_distance_km, current)
        goal = None
    fast_easy = format_pace(1000 / speed_for_vo2(EASY_FRACTIONS[1] * current)).removesuffix("/km")
    return {
        "current_vdot": round(current, 1),
        "goal_vdot": goal,
        "easy": f"{fast_easy}-{pace_for_fraction(current, EASY_FRACTIONS[0])}",
        "marathon": pace_for_fraction(current, MARATHON_FRACTION),
        "threshold": pace_for_fraction(current, THRESHOLD_FRACTION),
        "interval": pace_for_fraction(current, INTERVAL_FRACTION),
        "repetition": pace_for_fraction(current, REPETITION_FRACTION),
        "race_pace": format_pace(goal_minutes / race_distance_km),
    }
