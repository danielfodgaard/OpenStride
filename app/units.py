"""Pace and time arithmetic, in one place so a pace is always written the same way."""
import re


def pace(speed_mps: float | None) -> str:
    """Metres per second as min/km, "" when there is no speed."""
    if not speed_mps or speed_mps <= 0:
        return ""
    seconds = round(1000 / speed_mps)
    return f"{seconds // 60}:{seconds % 60:02d}/km"


def pace_of(distance_km: float | None, seconds: float | None) -> str:
    return pace(distance_km * 1000 / seconds) if distance_km and seconds else ""


def pace_seconds(text: str) -> list[int]:
    """Every m:ss in a pace such as "4:15-4:25/km", as seconds per km."""
    return [int(minutes) * 60 + int(seconds) for minutes, seconds in re.findall(r"(\d+):(\d\d)", text or "")]


def clock(seconds: float | None) -> str:
    """Seconds as h:mm:ss or m:ss."""
    total = round(seconds or 0)
    hours, rest = divmod(total, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"
