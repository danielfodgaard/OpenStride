from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import generator, lifecycle, main, metrics, progress, storage
from app.models import PlanFile
from test_review import reply as review_reply

ROOT = Path("examples/activities")


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return review_reply()


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-11")
    messages = FakeMessages()
    monkeypatch.setattr(generator, "make_client", lambda: SimpleNamespace(messages=messages))
    return messages


@pytest.fixture
def client():
    return TestClient(main.app)


def add_plan(plan_id="plan", path="examples/sample_plan_v3.json"):
    plan_file = PlanFile.model_validate_json(Path(path).read_text())
    plan_file.meta.id = plan_id
    storage.save_plan(plan_file)
    return plan_file


def upload(client, name):
    path = ROOT / name
    return client.post("/api/activities", files=[("files", (path.name, path.read_bytes()))]).json()[0]


def test_import_with_no_active_plan_is_unplanned_and_still_reviewed(client, setup):
    result = upload(client, "2026-11-05_easy.gpx")
    assert result["status"] == "imported" and result["activity"]["match"]["session_index"] == -1
    assert len(setup.calls) == 1
    prompt = setup.calls[0]["messages"][0]["content"]
    assert "Planned session:\nUnplanned run" in prompt and "no active plan" in prompt
    item = client.get("/api/activities").json()[0]
    assert item["match_label"] == "Unplanned" and item["review"]


def test_log_with_no_active_plan(client):
    entry = client.post("/api/manual", json={"date": "2026-11-03", "modality": "strength", "duration_min": 30}).json()
    assert entry["plan_id"] == "" and entry["title"] == "Strength"
    assert client.get("/api/activities").json()[0]["id"] == entry["id"]


def test_list_is_everything_newest_first_with_plan_and_session(client):
    add_plan()
    lifecycle.accept("plan")
    upload(client, "2026-11-05_easy.gpx")
    upload(client, "2026-11-10_tempo_bad.fit")
    client.post("/api/plans/plan/manual", json={"date": "2026-11-03", "modality": "strength", "duration_min": 30})
    items = client.get("/api/activities").json()
    assert [i["date"] for i in items] == ["2026-11-10", "2026-11-05", "2026-11-03"]
    newest = items[0]
    assert newest["title"] == "Tempo 5 km" and newest["type"] == "tempo"
    assert newest["match_label"].endswith("· Week 2 Tue") and newest["review"]["review"]["headline"]
    assert items[2]["manual"] and items[2]["title"] == "Strength A"


def test_list_can_still_filter_by_plan(client):
    upload(client, "2026-11-05_easy.gpx")
    add_plan()
    lifecycle.accept("plan")
    upload(client, "2026-11-10_tempo_bad.fit")
    assert len(client.get("/api/activities").json()) == 2
    assert len(client.get("/api/activities", params={"plan_id": "plan"}).json()) == 1


def test_workout_detail_for_file_and_log(client):
    add_plan()
    lifecycle.accept("plan")
    upload(client, "2026-11-10_tempo_bad.fit")
    entry = client.post("/api/plans/plan/manual", json={"date": "2026-11-03", "modality": "strength", "duration_min": 30}).json()
    file_id = storage.list_activities("plan")[0].id
    detail = client.get(f"/api/activities/{file_id}").json()
    assert detail["has_series"] and detail["has_raw"] and detail["match"]["week_number"] == 2 and detail["laps"]
    logged = client.get(f"/api/activities/{entry['id']}").json()
    assert logged["manual"] and not logged["has_series"]
    assert client.get("/api/activities/nope").status_code == 404


def test_delete_removes_all_four_files_and_recomputes(client):
    plan_file = add_plan()
    lifecycle.accept("plan")
    upload(client, "2026-11-10_tempo_bad.fit")
    activity_id = storage.list_activities("plan")[0].id
    before = metrics.compute(storage.load_plan("plan")).weeks[1]
    assert before.actual_km == 6.0

    paths = [
        storage.activity_path(activity_id), storage.review_path(activity_id),
        storage.series_path(activity_id), storage.find_raw(activity_id),
    ]
    assert all(path.exists() for path in paths)
    assert client.delete(f"/api/activities/{activity_id}").status_code == 204
    assert not any(path.exists() for path in paths)
    assert client.get("/api/activities").json() == []
    cache = (storage.plan_dir("plan") / "metrics_cache.json").read_text()
    assert '"actual_km": 6.0' not in cache
    assert client.delete(f"/api/activities/{activity_id}").status_code == 404


def test_delete_of_an_unattached_workout(client):
    upload(client, "2026-11-05_easy.gpx")
    activity_id = storage.list_all_activities()[0].id
    assert client.delete(f"/api/activities/{activity_id}").status_code == 204


def test_a_deleted_run_can_be_imported_again(client):
    upload(client, "2026-11-05_easy.gpx")
    client.delete(f"/api/activities/{storage.list_all_activities()[0].id}")
    assert upload(client, "2026-11-05_easy.gpx")["status"] == "imported"


def test_an_unattached_workout_joins_the_plan_it_is_matched_to(client):
    upload(client, "2026-11-05_easy.gpx")
    add_plan()
    plan_file = lifecycle.accept("plan")
    activity_id = storage.list_all_activities()[0].id
    index = next(i for i, s in enumerate(plan_file.plan.weeks[0].sessions) if s.day == "Thu" and s.modality == "run")
    edited = client.patch(f"/api/activities/{activity_id}", json={"match": {"week_number": 1, "session_index": index}}).json()
    assert edited["plan_id"] == "plan"
    assert metrics.compute(storage.load_plan("plan")).weeks[0].actual_km > 8


def test_an_unattached_log_joins_the_plan_it_is_matched_to(client):
    entry = client.post("/api/manual", json={"date": "2026-11-03", "modality": "strength", "duration_min": 30}).json()
    add_plan()
    plan_file = lifecycle.accept("plan")
    index = next(i for i, s in enumerate(plan_file.plan.weeks[0].sessions) if s.modality == "strength")
    edited = client.patch(f"/api/manual/{entry['id']}", json={"match": {"week_number": 1, "session_index": index}}).json()
    assert edited["plan_id"] == "plan" and len(storage.list_manual("plan")) == 1 and storage.list_manual("") == []


def test_matching_with_no_plan_at_all_is_refused(client):
    upload(client, "2026-11-05_easy.gpx")
    activity_id = storage.list_all_activities()[0].id
    response = client.patch(f"/api/activities/{activity_id}", json={"match": {"week_number": 1, "session_index": 0}})
    assert response.status_code == 409


def test_status_counts_to_date(client):
    add_plan()
    plan_file = lifecycle.accept("plan")
    upload(client, "2026-11-03_tempo.fit")
    client.post("/api/plans/plan/manual", json={"date": "2026-11-03", "modality": "strength", "duration_min": 30})
    status = progress.plan_status(storage.load_plan("plan"), date(2026, 11, 11))

    assert (status["week_number"], status["weeks_total"], status["phase"]) == (2, 10, "base")
    assert status["days_to_race"] == 60 and status["started"] and not status["finished"]
    planned = [s for w in plan_file.plan.weeks for s in w.sessions if s.date <= date(2026, 11, 11)]
    assert status["sessions_planned"] == len(planned) and status["sessions_completed"] == 2
    assert status["km_planned"] == pytest.approx(sum(s.distance_km for s in planned if s.modality == "run"), abs=0.1)
    assert status["km_actual"] == 8.0
    assert (status["strength_completed"], status["strength_planned"]) == (1, 3)
    assert (status["cross_completed"], status["cross_planned"]) == (0, 2)


def test_next_up_is_todays_session_then_the_next_one(client):
    add_plan()
    plan_file = lifecycle.accept("plan")
    assert progress.plan_status(plan_file, date(2026, 11, 11))["next"]["title"].startswith("Easy ride")
    rest_day = progress.plan_status(plan_file, date(2026, 11, 13))["next"]
    assert rest_day["date"] == "2026-11-14" and not rest_day["done"]


def test_a_done_session_today_shows_as_done_with_its_numbers(client, monkeypatch):
    add_plan()
    lifecycle.accept("plan")
    upload(client, "2026-11-05_easy.gpx")
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-05")
    client.post("/api/plans/plan/manual", json={"date": "2026-11-05", "modality": "strength", "duration_min": 30})
    status = progress.plan_status(storage.load_plan("plan"), date(2026, 11, 5))
    finished = status["today_done"]
    assert finished["done"] and finished["workout_id"] and finished["actual_km"] > 8
    assert finished["date"] == "2026-11-05" and finished["actual_pace"]
    assert not status["next"]["done"] and status["next"]["date"] > "2026-11-05"


def test_status_before_the_plan_and_after_the_race():
    plan_file = add_plan()
    before = progress.plan_status(plan_file, date(2026, 10, 31))
    assert not before["started"] and before["week_number"] == 0 and before["next"]["date"] == "2026-11-03"
    after = progress.plan_status(plan_file, date(2027, 2, 1))
    assert after["finished"] and after["next"] is None and after["days_to_race"] < 0
