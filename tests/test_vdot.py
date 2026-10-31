import re

import pytest

from app.vdot import compute_paces, format_pace, parse_time, predict_time, vdot


def seconds_per_km(pace):
    minutes, seconds = re.match(r"(\d+):(\d\d)/km$", pace).groups()
    return int(minutes) * 60 + int(seconds)


def test_half_marathon_vdot():
    assert vdot(21.0975, parse_time("1:30:00")) == pytest.approx(51.0, abs=0.5)


def test_5k_vdot():
    assert vdot(5, parse_time("20:00")) == pytest.approx(49.8, abs=0.5)


def test_paces_are_monotonic():
    paces = compute_paces(21.0975, "1:30:00", 21.0975, None)
    fast_easy, slow_easy = paces["easy"].split("-")
    order = [
        slow_easy,
        fast_easy + "/km",
        paces["marathon"],
        paces["threshold"],
        paces["interval"],
        paces["repetition"],
    ]
    seconds = [seconds_per_km(p) for p in order]
    assert seconds == sorted(seconds, reverse=True) and len(set(seconds)) == len(seconds)


def test_round_trip_prediction():
    assert predict_time(21.0975, 51) == pytest.approx(90, abs=1)


def test_race_pace_uses_goal_time_when_given():
    paces = compute_paces(10, "42:00", 21.0975, "1:30:00")
    assert paces["race_pace"] == "4:16/km"
    assert paces["goal_vdot"] == pytest.approx(51.0, abs=0.5)


def test_race_pace_is_predicted_without_goal_time():
    paces = compute_paces(21.0975, "1:30:00", 21.0975, None)
    assert paces["race_pace"] == "4:16/km"
    assert paces["goal_vdot"] is None


def test_pace_rounding_carries_into_minutes():
    assert format_pace(4 + 59.7 / 60) == "5:00/km"


@pytest.mark.parametrize("bad", ["", "90", "1:2:3:4", "12:60", "1:60:00", "0:00", "-1:00", "a:bc", "٣:٠٠"])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


@pytest.mark.parametrize("text,minutes", [("20:00", 20), ("1:30:00", 90), ("95:30", 95.5)])
def test_parse_time_accepts(text, minutes):
    assert parse_time(text) == minutes
