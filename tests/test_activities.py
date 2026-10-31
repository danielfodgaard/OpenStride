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
