from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, lifecycle, main, storage
from app.models import PlanFile

SAMPLES = Path("examples/activities")


def read(name):
    return (SAMPLES / name).read_bytes()


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)


def add_draft(plan_id="plan"):
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = plan_id
    storage.save_plan(plan_file)
    return plan_file


@pytest.fixture
def active():
    add_draft()
    return lifecycle.accept("plan")


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.mark.parametrize(
    "name,km,moving,elapsed,day",
    [
        ("2026-11-03_tempo.fit", 8.0, 2488, 2488, date(2026, 11, 3)),
        ("2026-11-05_easy.gpx", 8.977, 3070, 3070, date(2026, 11, 5)),
        ("2026-11-06_shakeout_unplanned.gpx", 3.0, 1080, 1080, date(2026, 11, 6)),
        ("2026-11-07_easy_no_hr.gpx", 9.0, 3150, 3150, date(2026, 11, 7)),
        ("2026-11-08_long.gpx", 15.977, 5560, 5680, date(2026, 11, 8)),
        ("2026-11-10_tempo_bad.fit", 6.0, 2020, 2020, date(2026, 11, 10)),
    ],
)
def test_sample_files_parse_to_known_values(name, km, moving, elapsed, day):
    activity = activities.parse_file(name, read(name), "plan")
    assert activity.distance_km == pytest.approx(km, rel=0.01)
    assert activity.moving_time_s == pytest.approx(moving, rel=0.01)
    assert activity.elapsed_time_s == pytest.approx(elapsed, rel=0.01)
    assert activity.date == day
    assert sum(s.distance_km for s in activity.km_splits) == pytest.approx(km, rel=0.01)
    assert sum(s.time_s for s in activity.km_splits) == pytest.approx(moving, rel=0.01)


def test_fit_laps_hr_and_cadence():
    activity = activities.parse_file("a.fit", read("2026-11-03_tempo.fit"), "plan")
    assert [(lap.distance_km, lap.pace) for lap in activity.laps] == [(2, "5:45/km"), (4, "4:37/km"), (2, "5:45/km")]
    assert activity.laps[1].avg_hr == pytest.approx(162, abs=2)
    assert activity.avg_hr == 148
    assert activity.avg_cadence_spm == 176
    assert activity.avg_pace == "5:11/km"
    assert activity.elevation_gain_m == 20


def test_fit_local_timestamp_sets_the_local_day():
    activity = activities.parse_file("a.fit", read("2026-11-03_tempo.fit"), "plan")
    assert activity.id == "2026-11-03T17-30-00"  # 01:30 UTC on the 4th at UTC-8


def test_gpx_hr_cadence_splits_and_elevation():
    activity = activities.parse_file("a.gpx", read("2026-11-08_long.gpx"), "plan")
    assert activity.laps == []
    assert activity.avg_hr == 142 and activity.avg_cadence_spm == 174
    assert activity.avg_pace == "5:47/km"  # the sample step straddling the pause counts as stopped
    assert [s.pace for s in activity.km_splits[:15]].count("5:48/km") == 13
    assert activity.elevation_gain_m == pytest.approx(16 * 12, rel=0.1)


def test_missing_hr_is_zero():
    activity = activities.parse_file("a.gpx", read("2026-11-07_easy_no_hr.gpx"), "plan")
    assert activity.avg_hr == activity.max_hr == activity.avg_cadence_spm == 0


def test_upload_matches_and_skips_duplicates(client, active):
    files = [("files", (n, read(n))) for n in ["2026-11-05_easy.gpx", "2026-11-06_shakeout_unplanned.gpx"]]
    results = client.post("/api/activities", files=files, data={"user_note": "felt fine", "rpe": "4"}).json()

    planned, unplanned = results[0]["activity"], results[1]["activity"]
    assert planned["match"] == {"planned_date": "2026-11-05", "week_number": 1, "session_index": 1}
    assert planned["user_note"] == "felt fine" and planned["rpe"] == 4
    assert unplanned["match"] == {"planned_date": "", "week_number": 0, "session_index": -1}

    again = client.post("/api/activities", files=files[:1]).json()
    assert again[0]["status"] == "duplicate"
    assert len(client.get("/api/activities", params={"plan_id": "plan"}).json()) == 2


def test_bad_file_does_not_stop_the_others(client, active):
    files = [
        ("files", ("broken.fit", b"not a fit file")),
        ("files", ("broken.gpx", b"<gpx><oops")),
        ("files", ("empty.gpx", b'<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"></gpx>')),
        ("files", ("notes.txt", b"hello")),
        ("files", ("2026-11-05_easy.gpx", read("2026-11-05_easy.gpx"))),
    ]
    statuses = [r["status"] for r in client.post("/api/activities", files=files).json()]
    assert statuses == ["error", "error", "error", "error", "imported"]


def test_several_sessions_on_a_day_picks_the_closest_distance(active):
    week = active.plan.weeks[0]
    extra = week.sessions[1].model_copy(update={"distance_km": 3.0})
    week.sessions.insert(1, extra)  # two sessions on Thu: 3 km and 9 km
    activity = activities.parse_file("a.gpx", read("2026-11-05_easy.gpx"), "plan")
    assert activities.match_activity(activity, active).session_index == 2
    activity.distance_km = 3.2
    assert activities.match_activity(activity, active).session_index == 1


def test_unplanned_on_a_rest_day(active):
    activity = activities.parse_file("a.gpx", read("2026-11-06_shakeout_unplanned.gpx"), "plan")
    assert activities.match_activity(activity, active).session_index == -1


def test_edit_note_rpe_and_match(client, active):
    client.post("/api/activities", files=[("files", ("a.gpx", read("2026-11-06_shakeout_unplanned.gpx")))])
    activity_id = "2026-11-06T" + storage.list_activities("plan")[0].id.split("T")[1]

    edited = client.patch(f"/api/activities/{activity_id}", json={
        "user_note": "swapped days", "rpe": 3, "match": {"week_number": 1, "session_index": 1},
    }).json()
    assert edited["user_note"] == "swapped days" and edited["rpe"] == 3
    assert edited["match"] == {"planned_date": "2026-11-05", "week_number": 1, "session_index": 1}

    cleared = client.patch(f"/api/activities/{activity_id}", json={"match": {"week_number": 0, "session_index": -1}})
    assert cleared.json()["match"]["session_index"] == -1
    assert client.patch(f"/api/activities/{activity_id}", json={"rpe": 11}).status_code == 422
    missing = client.patch(f"/api/activities/{activity_id}", json={"match": {"week_number": 1, "session_index": 9}})
    assert missing.status_code == 409


def test_upload_needs_an_active_plan(client):
    add_draft()
    files = [("files", ("a.gpx", read("2026-11-05_easy.gpx")))]
    assert client.post("/api/activities", files=files).status_code == 409


def test_activity_on_an_archived_plan_is_read_only(client, active):
    client.post("/api/activities", files=[("files", ("a.gpx", read("2026-11-05_easy.gpx")))])
    activity_id = storage.list_activities("plan")[0].id
    add_draft("next")
    lifecycle.accept("next")
    assert client.patch(f"/api/activities/{activity_id}", json={"rpe": 5}).status_code == 409
    assert len(client.get("/api/activities", params={"plan_id": "plan"}).json()) == 1


def test_splits_interpolate_a_pause_out_of_moving_time():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    points = [
        activities.Point(t0, 0.0),
        activities.Point(t0 + timedelta(seconds=300), 1.0),
        activities.Point(t0 + timedelta(seconds=900), 1.0),  # stopped for ten minutes
        activities.Point(t0 + timedelta(seconds=1200), 2.0),
    ]
    moving = activities.moving_seconds(points)
    assert moving[-1] == 600
    assert [s.time_s for s in activities.km_splits(points, moving)] == [300, 300]


CROSS = SAMPLES / "cross"
GPX_HEAD = '<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"><trk>'
GPX_BODY = (
    '<trkseg><trkpt lat="56.0" lon="10.0"><time>2026-11-05T12:00:00Z</time></trkpt>'
    '<trkpt lat="56.0" lon="10.01"><time>2026-11-05T12:03:00Z</time></trkpt></trkseg></trk></gpx>'
)


def add_v3_plan():
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "v3"
    storage.save_plan(plan_file)
    return lifecycle.accept("v3")


def test_cycling_fit_is_cross_training():
    activity = activities.parse_file("a.fit", (CROSS / "2026-11-04_ride.fit").read_bytes(), "plan")
    assert activity.sport == "cycling" and activity.modality == "cross"
    assert activity.distance_km == 21.0 and activity.moving_time_s == 2700


def test_cycling_matches_the_cross_session_and_never_a_run():
    plan_file = add_v3_plan()
    ride = activities.parse_file("a.fit", (CROSS / "2026-11-04_ride.fit").read_bytes(), "v3")
    match = activities.match_activity(ride, plan_file)
    assert plan_file.plan.weeks[0].sessions[match.session_index].type == "cross_training"

    # The same ride on a run day has no cross session to match, so it stays unplanned.
    sunday = ride.model_copy(update={"date": date(2026, 11, 8)})
    assert activities.match_activity(sunday, plan_file).session_index == -1


def test_a_run_never_matches_strength_or_cross():
    plan_file = add_v3_plan()
    run = activities.parse_file("a.fit", read("2026-11-03_tempo.fit"), "v3")  # Tue has a run and strength
    session = plan_file.plan.weeks[0].sessions[activities.match_activity(run, plan_file).session_index]
    assert session.modality == "run"
    wednesday = run.model_copy(update={"date": date(2026, 11, 4)})  # Wed has only a ride
    assert activities.match_activity(wednesday, plan_file).session_index == -1


def test_a_run_cannot_be_matched_to_strength_by_hand(client):
    add_v3_plan()
    client.post("/api/activities", files=[("files", ("a.fit", read("2026-11-03_tempo.fit")))])
    activity_id = storage.list_activities("v3")[0].id
    plan_file = storage.load_plan("v3")
    index = next(i for i, s in enumerate(plan_file.plan.weeks[0].sessions) if s.modality == "strength")
    response = client.patch(f"/api/activities/{activity_id}", json={"match": {"week_number": 1, "session_index": index}})
    assert response.status_code == 409


def test_pool_swim_without_records_uses_the_session_totals():
    activity = activities.parse_file("a.fit", (CROSS / "2026-11-12_pool_swim.fit").read_bytes(), "plan")
    assert activity.sport == "swimming" and activity.modality == "cross"
    assert activity.distance_km == 1.5 and activity.moving_time_s == 1800
    assert activity.km_splits == [] and activity.avg_hr == 130 and activity.date == date(2026, 11, 12)


@pytest.mark.parametrize(
    "kind,sport,modality",
    [
        ("running", "running", "run"),
        ("trail_running", "running", "run"),
        ("Run", "running", "run"),
        ("9", "running", "run"),
        ("cycling", "cycling", "cross"),
        ("1", "cycling", "cross"),
        ("hiking", "hiking", "cross"),
    ],
)
def test_gpx_type(kind, sport, modality):
    content = f"{GPX_HEAD}<type>{kind}</type>{GPX_BODY}".encode()
    activity = activities.parse_file("a.gpx", content, "plan")
    assert (activity.sport, activity.modality) == (sport, modality)


def test_gpx_without_a_type_is_a_run():
    assert activities.parse_file("a.gpx", f"{GPX_HEAD}{GPX_BODY}".encode(), "plan").modality == "run"


def test_gpx_tolerates_a_bom_and_decimal_extensions():
    content = (
        '﻿<?xml version="1.0" encoding="UTF-8"?>' + GPX_HEAD.replace("<trk>", '<trk>')
        + GPX_BODY.replace("<time>2026-11-05T12:00:00Z</time>", "<time>2026-11-05T12:00:00Z</time><extensions><hr>150.0</hr><cadence>85.5</cadence></extensions>")
    ).encode("utf-8")
    activity = activities.parse_file("a.gpx", content, "plan")
    assert activity.avg_hr == 150 and activity.avg_cadence_spm == 172


@pytest.mark.parametrize("cadence,expected", [(0, 0), (88, 176), (120, 240), (176, 176)])
def test_cadence_in_strides_or_steps(cadence, expected):
    assert activities.steps_per_minute(cadence) == expected


def test_distance_counters_that_do_not_start_at_zero():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    points = [activities.Point(t0 + timedelta(seconds=300 * i), 5.0 + i) for i in range(3)]
    parsed = activities.Parsed(points=points)
    activity = activities.summarise(parsed, "a", "plan", "", 0)
    assert [s.time_s for s in activity.km_splits] == [300, 300]


def test_fit_records_with_gps_but_no_distance():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    semicircle = 2**31 / 180
    records = [
        {"timestamp": t0, "position_lat": 56 * semicircle, "position_long": 10 * semicircle},
        {"timestamp": t0 + timedelta(seconds=60), "position_lat": 56.01 * semicircle, "position_long": 10 * semicircle},
    ]
    assert activities.fit_points(records)[-1].distance_km == pytest.approx(1.112, abs=0.01)


def test_truncated_fit_keeps_what_was_recorded():
    content = read("2026-11-03_tempo.fit")
    activity = activities.parse_file("a.fit", content[: len(content) // 2], "plan")
    assert 0 < activity.distance_km < 8


def test_bad_checksum_is_not_fatal():
    content = bytearray(read("2026-11-03_tempo.fit"))
    content[-1] ^= 0xFF
    assert activities.parse_file("a.fit", bytes(content), "plan").distance_km == 8.0


def test_same_run_as_fit_and_gpx_is_a_duplicate(client, active):
    client.post("/api/activities", files=[("files", ("a.gpx", read("2026-11-05_easy.gpx")))])
    start = storage.list_activities("plan")[0].start_time
    other = activities.parse_file("a.gpx", read("2026-11-05_easy.gpx"), "plan")
    assert activities.find_duplicate(other.model_copy(update={"start_time": start + timedelta(seconds=45)}))
    assert not activities.find_duplicate(other.model_copy(update={"start_time": start + timedelta(minutes=5)}))


def test_bike_cadence_is_not_doubled():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    points = [activities.Point(t0 + timedelta(seconds=60 * i), i * 0.5, cadence=85) for i in range(3)]
    activity = activities.summarise(activities.Parsed(points=points, sport="cycling"), "a", "plan", "", 0)
    assert activity.avg_cadence_spm == 85


def test_garbage_timestamps_are_dropped_and_partial_records_give_no_splits():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    frames = [{"timestamp": t0 + timedelta(seconds=30 * i), "distance": 100.0 * i} for i in range(4)]
    frames.append({"timestamp": datetime(2085, 1, 1, tzinfo=timezone.utc), "distance": 400.0})
    session = {"start_time": t0, "total_elapsed_time": 3000, "total_timer_time": 3000, "total_distance": 11000}
    original = activities.read_fit_frames
    activities.read_fit_frames = lambda content: (frames, [], [session], {}, {"type": "activity"})
    try:
        parsed = activities.parse_fit(b"")
    finally:
        activities.read_fit_frames = original
    assert len(parsed.points) == 4
    activity = activities.summarise(parsed, "a", "plan", "", 0)
    assert activity.distance_km == 11 and activity.km_splits == []


def test_out_of_order_times_never_give_moving_above_elapsed():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    seconds = [0, 300, 600, 60]
    points = [activities.Point(t0 + timedelta(seconds=s), i * 1.0) for i, s in enumerate(seconds)]
    activity = activities.summarise(activities.Parsed(points=points), "a", "plan", "", 0)
    assert activity.moving_time_s <= activity.elapsed_time_s


def stop_and_go():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    return [
        activities.Point(t0, 0.0),
        activities.Point(t0 + timedelta(seconds=300), 1.0),
        activities.Point(t0 + timedelta(seconds=420), 1.0),  # two minutes standing still
        activities.Point(t0 + timedelta(seconds=720), 2.0),
    ]


def test_gpx_has_no_timer_so_stopped_time_comes_out():
    assert activities.moving_seconds(stop_and_go())[-1] == 600


def test_a_running_watch_timer_keeps_stopped_time_in_the_splits():
    assert activities.moving_seconds(stop_and_go(), 720)[-1] == 720


def test_a_paused_watch_timer_takes_out_what_it_paused():
    assert activities.moving_seconds(stop_and_go(), 600)[-1] == 600


def test_impossible_dates_are_refused():
    content = f"{GPX_HEAD}{GPX_BODY}".replace("2026-11-05", "1901-12-14").encode()
    with pytest.raises(ValueError, match="timestamps look wrong"):
        activities.parse_file("a.gpx", content, "plan")


def test_gpx_declared_as_latin_1():
    content = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>' + f"{GPX_HEAD}<name>L\u00f8b</name>{GPX_BODY}"
    ).encode("latin-1")
    assert activities.parse_file("a.gpx", content, "plan").distance_km > 0


def test_a_run_can_be_imported_again_under_a_new_plan(client, active):
    files = [("files", ("a.gpx", read("2026-11-05_easy.gpx")))]
    client.post("/api/activities", files=files)
    add_draft("next")
    lifecycle.accept("next")
    result = client.post("/api/activities", files=files).json()[0]
    assert result["status"] == "imported"
    assert [len(storage.list_activities(p)) for p in ("plan", "next")] == [1, 1]
    assert storage.list_activities("plan")[0].id != storage.list_activities("next")[0].id
