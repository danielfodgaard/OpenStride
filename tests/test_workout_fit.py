import glob
import io
import struct
from datetime import date
from pathlib import Path

import fitdecode
import pytest
from garminconnect.workout import RunningWorkout

from app import activities, workout_fit
from app.models import DatedSession, PlanFile, Step

PLANS = ["examples/sample_plan_v3.json", *glob.glob("examples/demo/data/plans/*/plan.json")]
EASY = "5:11-6:13/km"


def plan(path=PLANS[1]):
    return PlanFile.model_validate_json(Path(path).read_text())


def run_sessions(path):
    return [(w.week_number, i, s) for w in plan(path).plan.weeks for i, s in enumerate(w.sessions) if s.modality == "run"]


def first(kind):
    return next(s for _, _, s in run_sessions(PLANS[1]) if s.type == kind)


def step(repeat=1, description="", distance_km=0.0, duration_min=0.0, target_pace=""):
    return Step(repeat=repeat, description=description, distance_km=distance_km, duration_min=duration_min, target_pace=target_pace, reps=0, load="")


def session(steps=(), title="Test", distance_km=0.0, duration_min=0.0, target_pace=""):
    return DatedSession(
        day="Tue", type="intervals", modality="run", title=title, distance_km=distance_km, duration_min=duration_min,
        target_pace=target_pace, description="", steps=list(steps), date=date(2026, 11, 3),
    )


def parse(data):
    """Every message, by fitdecode with CRC checking on: a bad checksum raises."""
    frames = {"file_id": [], "workout": [], "workout_step": []}
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                values = {f.name: f.value for f in frame.fields}
                # fitdecode names a field by the subfield it resolves to.
                for name, general in (("duration_distance", "duration_value"), ("duration_time", "duration_value"), ("duration_step", "duration_value"),
                                      ("repeat_steps", "target_value"), ("target_speed_zone", "target_value")):
                    if name in values:
                        values[general] = values[name]
                frames.setdefault(frame.name, []).append(values)
    return frames


def fit_steps(item):
    return parse(workout_fit.build(item).data)["workout_step"]


def expand_fit(steps):
    """What a watch runs: follow repeat steps back through the parsed file."""
    order, i, left = [], 0, {}
    while i < len(steps):
        row = steps[i]
        if row["duration_type"] != "repeat_until_steps_cmplt":
            order.append(row)
            i += 1
            continue
        left.setdefault(i, row["target_value"] - 1)
        if left[i]:
            left[i] -= 1
            i = row["duration_value"]
        else:
            del left[i]
            i += 1
    return order


def expand_steps(steps):
    """Port of expandSteps in static/render/format.js."""
    expanded, i = [], 0
    while i < len(steps):
        one, two = steps[i], steps[i + 1] if i + 1 < len(steps) else None
        if one.repeat > 1 and two and two.repeat > 1 and abs(one.repeat - two.repeat) <= 1:
            for n in range(max(one.repeat, two.repeat)):
                expanded += [one] * (n < one.repeat) + [two] * (n < two.repeat)
            i += 2
        else:
            expanded += [one] * one.repeat
            i += 1
    return expanded


def test_file_id_has_what_a_watch_needs():
    frames = parse(workout_fit.build(first("easy")).data)
    [file_id] = frames["file_id"]
    assert file_id["type"] == "workout" and file_id["manufacturer"] == "development"
    assert file_id["product"] == 0 and file_id["serial_number"]
    assert file_id["time_created"].year >= 2026


def test_the_easy_run_is_one_step_with_the_easy_range():
    easy = first("easy")
    frames = parse(workout_fit.build(easy).data)
    [workout] = frames["workout"]
    [row] = frames["workout_step"]
    assert workout["sport"] == "running" and workout["num_valid_steps"] == 1 and workout["wkt_name"] == easy.title
    assert row["message_index"] == 0 and row["duration_type"] == "distance"
    assert row["duration_value"] == pytest.approx(easy.distance_km * 1000)
    assert row["target_type"] == "speed" and row["target_value"] == 0
    assert row["custom_target_speed_low"] == pytest.approx(2.68, abs=0.01)
    assert row["custom_target_speed_high"] == pytest.approx(3.22, abs=0.01)
    assert row["intensity"] == "active"


def test_raw_units_are_cm_ms_and_mm_per_s():
    [row] = workout_fit.step_rows([workout_fit.WatchStep("active", 800, 0, (2.5, 3.0))])
    assert row == (0, workout_fit.DURATION_DISTANCE, 80000, workout_fit.TARGET_SPEED, 0, 2500, 3000, 0)
    [row] = workout_fit.step_rows([workout_fit.WatchStep("active", 0, 90)])
    assert row == (0, workout_fit.DURATION_TIME, 90000, workout_fit.TARGET_OPEN, 0, 0, 0, 0)


def test_tempo_has_warm_up_work_and_cool_down():
    tempo = first("tempo")
    rows = fit_steps(tempo)
    assert [r["intensity"] for r in rows] == ["warmup", "active", "cooldown"]
    assert [r["duration_value"] for r in rows] == pytest.approx([s.distance_km * 1000 for s in tempo.steps])
    assert rows[1]["custom_target_speed_low"] == pytest.approx(1000 / (277 + 5), abs=0.001)
    assert rows[1]["custom_target_speed_high"] == pytest.approx(1000 / (277 - 5), abs=0.001)


def test_intervals_repeat_with_the_uneven_jog_count():
    rows = fit_steps(first("intervals"))  # warm up, 4 × 0.4, 3 × 0.4 jog, cool down
    assert [r["intensity"] for r in rows] == ["warmup", "active", "recovery", "active", "active", "cooldown"]
    [repeat] = [r for r in rows if r["duration_type"] == "repeat_until_steps_cmplt"]
    assert repeat["message_index"] == 3 and repeat["duration_value"] == 1
    assert repeat["target_value"] == 3 and repeat["target_type"] == "open"
    assert rows[4]["duration_type"] == "distance"  # the last rep, with no jog after it
    assert len(expand_fit(rows)) == 1 + 4 + 3 + 1


def test_an_even_pair_is_one_block():
    pair = session([step(6, "Interval", 0.8, 0, "4:09/km"), step(6, "Jog recovery", 0.4, 0, EASY)])
    rows = fit_steps(pair)
    assert len(rows) == 3 and rows[2]["target_value"] == 6 and rows[2]["duration_value"] == 0
    assert [r["intensity"] for r in expand_fit(rows)] == ["active", "recovery"] * 6


def test_an_unpaired_repeat_wraps_itself():
    rows = fit_steps(session([step(5, "Strides", 0, 0.5)]))
    assert len(rows) == 2 and rows[1]["target_value"] == 5 and rows[1]["duration_value"] == 0
    assert rows[0]["duration_value"] == pytest.approx(30)


@pytest.mark.parametrize("repeats", [(4, 3), (3, 4), (6, 6), (2, 2), (5, 1), (1, 5), (6, 2), (2, 3, 4), (3, 3, 3, 3), (4, 0, 3), (2,), (1,)])
def test_expansion_agrees_with_the_workout_page(repeats):
    steps = [step(n, f"Part {i}", 0.1 * (i + 1)) for i, n in enumerate(repeats)]
    want = [round(s.distance_km * 1000) for s in expand_steps(steps)]
    assert [round(r["duration_value"]) for r in expand_fit(fit_steps(session(steps)))] == want


@pytest.mark.parametrize("path", PLANS)
def test_every_run_session_round_trips(path):
    sessions = run_sessions(path)
    assert len(sessions) > 30
    for _, _, item in sessions:
        built = workout_fit.build(item)
        frames = parse(built.data)
        assert built.notes == []
        rows = frames["workout_step"]
        assert frames["workout"][0]["num_valid_steps"] == len(rows)
        assert [r["message_index"] for r in rows] == list(range(len(rows)))
        assert len(frames["workout"][0]["wkt_name"]) <= workout_fit.NAME_LIMIT
        expanded = expand_fit(rows)
        if item.steps:
            assert [r["duration_value"] for r in expanded] == pytest.approx([s.distance_km * 1000 for s in expand_steps(item.steps)])
        assert sum(r["duration_value"] for r in expanded) == pytest.approx(item.distance_km * 1000)
        assert all(r["target_type"] == "speed" and r["custom_target_speed_low"] < r["custom_target_speed_high"] for r in expanded)


@pytest.mark.parametrize("path", PLANS)
def test_every_run_session_is_valid_connect_json(path):
    for _, _, item in run_sessions(path):
        workout = RunningWorkout.model_validate(workout_fit.garmin_json(item))
        assert workout.to_dict()["workoutName"] == workout_fit.workout_name(item)


def test_connect_json_matches_the_file():
    data = workout_fit.garmin_json(first("intervals"))
    warm, group, last, cool = data["workoutSegments"][0]["workoutSteps"]
    assert group["type"] == "RepeatGroupDTO" and group["numberOfIterations"] == 3
    assert [s["stepType"]["stepTypeKey"] for s in group["workoutSteps"]] == ["interval", "recovery"]
    assert last["endConditionValue"] == 400 and last["stepType"]["stepTypeKey"] == "interval"
    assert [warm["stepOrder"], group["stepOrder"], *(s["stepOrder"] for s in group["workoutSteps"]), last["stepOrder"], cool["stepOrder"]] == [1, 2, 3, 4, 5, 6]
    assert warm["targetValueOne"] < warm["targetValueTwo"] and warm["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert data["estimatedDurationInSecs"] > 1800


@pytest.mark.parametrize("pace,band", [
    ("5:11-6:13/km", (311, 373)),
    ("6:13-5:11/km", (311, 373)),
    ("4:30/km", (265, 275)),
    ("4:30", (265, 275)),
])
def test_speed_band_follows_the_page(pace, band):
    assert workout_fit.speed_band(pace) == pytest.approx((1000 / band[1], 1000 / band[0]))


@pytest.mark.parametrize("pace", ["", None, "easy", "0:03/km", "fast!"])
def test_an_unreadable_pace_is_no_band(pace):
    assert workout_fit.speed_band(pace) is None


def test_an_unreadable_pace_runs_with_no_target_and_a_note():
    built = workout_fit.build(session([step(1, "Hard bit", 1.0, 0, "comfortably hard")]))
    [row] = parse(built.data)["workout_step"]
    assert row["target_type"] == "open" and len(built.notes) == 1 and "comfortably hard" in built.notes[0]


def test_no_pace_at_all_needs_no_note():
    assert workout_fit.build(session([step(1, "Run", 1.0)])).notes == []


def test_time_steps_and_open_steps():
    rows = fit_steps(session([step(1, "Warm up", 0, 10), step(1, "Until it feels right")]))
    assert rows[0]["duration_type"] == "time" and rows[0]["duration_value"] == 600
    assert rows[1]["duration_type"] == "open"


def test_a_session_with_only_a_duration():
    [row] = fit_steps(session(duration_min=40, target_pace=EASY))
    assert row["duration_type"] == "time" and row["duration_value"] == 2400


@pytest.mark.parametrize("description,kind", [
    ("Warm up", "warmup"), ("WARM-UP", "warmup"), ("Cool down", "cooldown"), ("Jog recovery", "recovery"),
    ("Recovery", "recovery"), ("Standing rest", "recovery"), ("Interval", "active"), ("Tempo", "active"),
    ("Restart at race pace", "active"), ("", "active"),
])
def test_intensity_comes_from_the_description(description, kind):
    assert workout_fit.intensity_of(description) == kind


@pytest.mark.parametrize("title,name", [
    ("Easy 8 km", "Easy 8 km"), ("6 × 1 km", "6x1 km"), ("Long run with race-pace finish", "Long run with r"), ("", "Run"), ("Løb på 5 km — søndag", "Løb på 5 km — s"),
])
def test_the_name_fits_the_watch(title, name):
    assert workout_fit.workout_name(session(title=title)) == name
    assert parse(workout_fit.build(session(title=title)).data)["workout"][0]["wkt_name"] == name


def test_a_workout_file_is_refused_by_the_activity_import():
    with pytest.raises(ValueError, match="workout file"):
        activities.parse_fit(workout_fit.build(first("easy")).data)


def test_the_header_is_the_fourteen_byte_form():
    data = workout_fit.build(first("easy")).data
    size, _, _, body, tag, crc = struct.unpack("<BBHI4sH", data[:14])
    assert size == 14 and tag == b".FIT" and crc == workout_fit.crc16(data[:12]) and len(data) == 14 + body + 2


def test_crc_is_the_arc_variant():
    assert workout_fit.crc16(b"123456789") == 0xBB3D


def test_fingerprint_follows_the_watch_not_the_wording():
    base = first("tempo").model_copy(deep=True)
    same = base.model_copy(update={"description": "Reworded"})
    moved = base.model_copy(update={"date": date(2030, 1, 1)})
    slower = base.model_copy(deep=True)
    slower.steps[1].target_pace = "5:00/km"
    assert workout_fit.fingerprint(base) == workout_fit.fingerprint(same)
    assert workout_fit.fingerprint(base) != workout_fit.fingerprint(moved)
    assert workout_fit.fingerprint(base) != workout_fit.fingerprint(slower)


def test_file_names_are_plain():
    assert workout_fit.file_name(session(title="6 × 1 km")) == "2026-11-03-6-1-km.fit"
