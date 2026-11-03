import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, adapt, chat, lifecycle, main, review, storage
from app.models import PlanFile
from app.series import build_series

sys.path.insert(0, "scripts")
import backfill_series  # noqa: E402

ROOT = Path("examples/activities")
NOT_CHARTABLE = ("pool_swim", "Mojstrovka")  # no records; bogus 1901 timestamps, refused at import
FILES = sorted(p for p in ROOT.rglob("*") if p.suffix in (".fit", ".gpx") and not any(n in p.name for n in NOT_CHARTABLE))


def make_series(path):
    parsed = activities.parse_bytes(path.name, path.read_bytes())
    return parsed, build_series("x", activities.rebased(parsed.points))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_series_from_every_sample(path):
    parsed, series = make_series(path)
    points = activities.rebased(parsed.points)
    span = (points[-1].time - points[0].time).total_seconds()
    assert len(series["points"]) == pytest.approx(span / 5 + 1, abs=1)
    assert series["points"][-1][0] == pytest.approx(points[-1].distance_km, rel=0.01, abs=0.01)


def test_a_90_minute_run_is_about_1000_points():
    _, series = make_series(ROOT / "sub17_5k_plan" / "2026-11-05_thu_long_long-run.fit")
    assert 1000 <= len(series["points"]) <= 1100


def test_point_shape():
    _, series = make_series(ROOT / "2026-11-03_tue_easy_easy-run.fit")
    assert series["step_s"] == 5
    km, elapsed, pace, hr, lat, lon = series["points"][100]
    assert elapsed == 500 and km == pytest.approx(1.49, abs=0.05) and 280 < pace < 420 and 100 < hr < 200
    assert -90 < lat < 90 and -180 < lon < 180


def test_pace_is_null_only_while_stopped():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    run = [activities.Point(t0 + timedelta(seconds=10 * i), i * 0.03) for i in range(30)]  # 3 m/s
    stop = [activities.Point(run[-1].time + timedelta(seconds=10 * i), run[-1].distance_km) for i in range(1, 19)]
    more = [activities.Point(stop[-1].time + timedelta(seconds=10 * i), run[-1].distance_km + i * 0.03) for i in range(1, 30)]
    rows = build_series("x", run + stop + more)["points"]
    stopped = [row[1] for row in rows if row[2] is None]
    assert stopped and min(stopped) > 290 and max(stopped) < 480  # inside the 180 s stop
    assert all(row[2] is not None for row in rows if row[1] < 290 or row[1] > 480)


def test_gpx_without_hr_gives_null_hr():
    _, series = make_series(ROOT / "2026-11-07_easy_no_hr.gpx")
    assert {row[3] for row in series["points"]} == {None}


def test_no_gps_gives_null_positions():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    rows = build_series("x", [activities.Point(t0 + timedelta(seconds=60 * i), i * 0.2) for i in range(4)])["points"]
    assert {row[4] for row in rows} == {None} and {row[5] for row in rows} == {None}


def test_a_file_with_no_records_has_no_series(tmp_path, monkeypatch):
    _, series = make_series(ROOT / "cross" / "2026-11-12_pool_swim.fit")
    assert series is None


@pytest.fixture
def active(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


@pytest.fixture
def client():
    return TestClient(main.app)


def upload(client, path):
    return client.post("/api/activities", files=[("files", (path.name, path.read_bytes()))]).json()[0]


def test_import_keeps_the_raw_file_and_writes_the_series(client, active):
    path = ROOT / "2026-11-05_easy.gpx"
    upload(client, path)
    activity_id = storage.list_activities("plan")[0].id
    assert storage.find_raw(activity_id).read_bytes() == path.read_bytes()
    body = client.get(f"/api/activities/{activity_id}/series").json()
    assert body["activity_id"] == activity_id and len(body["points"]) > 100
    assert client.get("/api/activities/nope/series").status_code == 404


def test_series_files_are_not_mistaken_for_activities(client, active):
    upload(client, ROOT / "2026-11-05_easy.gpx")
    assert len(storage.list_all_activities()) == 1


def test_stored_start_is_utc_while_the_date_is_local(monkeypatch):
    # This file has no time zone of its own, so the server's applies. 23:12 UTC is 07:12 next day in +8.
    monkeypatch.setenv("TZ", "Asia/Singapore")
    time.tzset()
    activity = activities.parse_file("a.fit", (ROOT / "2026-11-03_tue_easy_easy-run.fit").read_bytes(), "plan")
    assert activity.start_utc == "2026-11-02T23:12:30+00:00"
    monkeypatch.undo()
    time.tzset()
    assert activity.date.isoformat() == "2026-11-03" and activity.date.weekday() == 1


def test_re_import_adds_the_series_to_an_older_workout(client, active):
    path = ROOT / "2026-11-05_easy.gpx"
    upload(client, path)
    activity = storage.list_activities("plan")[0]
    storage.series_path(activity.id).unlink()
    storage.find_raw(activity.id).unlink()
    activity.start_utc = ""
    storage.save_activity(activity)

    assert upload(client, path)["status"] == "updated"
    assert storage.has_series(activity.id) and storage.find_raw(activity.id)
    assert storage.load_activity(activity.id).start_utc
    assert len(storage.list_activities("plan")) == 1
    assert upload(client, path)["status"] == "duplicate"


def test_backfill_builds_from_raw_files(client, active):
    upload(client, ROOT / "2026-11-05_easy.gpx")
    activity_id = storage.list_activities("plan")[0].id
    storage.series_path(activity_id).unlink()
    assert backfill_series.backfill() == [activity_id]
    assert storage.has_series(activity_id)
    assert backfill_series.backfill() == []


def test_series_never_reaches_a_prompt(client, active):
    from test_chat_actions import reply_text
    from test_generator import FakeClient, reply

    upload(client, ROOT / "2026-11-03_tempo.fit")
    activity = storage.list_activities("plan")[0]
    row = storage.load_series(activity.id)["points"][50]
    marker = json.dumps(row[:2])[1:-1]
    no_change = {"recommendation": "no_change", "rationale": "ok", "changes": [], "weeks": []}
    verdict = {"completion": "as_planned", "headline": "x", "observations": [], "flags": [], "effort": "as_planned"}
    fake = FakeClient(reply_text("ok"), reply(no_change), reply(verdict))

    chat.send("plan", "hello", fake)
    adapt.create_proposal("plan", "this_week", "", fake)
    review.review_activity(activity, fake)

    assert len(fake.prompts) == 3
    for prompt in fake.prompts:
        assert "step_s" not in prompt and '"points"' not in prompt and marker not in prompt
