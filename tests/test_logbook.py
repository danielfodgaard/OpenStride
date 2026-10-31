from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import adapt, lifecycle, logbook, main, metrics, progress, storage
from app.models import PlanFile, Step
from test_adapt_merge import propose

TUESDAY = "2026-11-03"
EXERCISES = [
    {"repeat": 3, "description": "split squat", "distance_km": 0, "duration_min": 0, "target_pace": "", "reps": 8, "load": "2×12 kg"},
    {"repeat": 3, "description": "calf raise", "distance_km": 0, "duration_min": 0, "target_pace": "", "reps": 12, "load": "bodyweight"},
]


@pytest.fixture(autouse=True)
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-11")
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


@pytest.fixture
def client():
    return TestClient(main.app)


def log(client, **fields):
    body = {"date": TUESDAY, "modality": "strength", "duration_min": 35, "rpe": 7, "details": EXERCISES} | fields
    return client.post("/api/plans/plan/manual", json=body)


def test_strength_matches_the_planned_session_and_takes_its_title(client, plan):
    entry = log(client).json()
    session = plan.plan.weeks[0].sessions[entry["match"]["session_index"]]
    assert session.type == "strength" and session.day == "Tue"
    assert entry["match"]["week_number"] == 1 and entry["title"] == "Strength A"


def test_a_given_title_is_kept(client):
    assert log(client, title="Gym with Sam").json()["title"] == "Gym with Sam"


def test_same_date_other_modality_is_unplanned(client):
    entry = log(client, date="2026-11-05", modality="cross").json()
    assert entry["match"]["session_index"] == -1


def test_no_session_that_day_is_unplanned(client):
    entry = log(client, date="2026-11-02").json()
    assert entry["match"]["session_index"] == -1 and entry["title"] == "Strength"


def test_manual_run_matches_a_planned_run(client, plan):
    entry = log(client, modality="run", distance_km=8, details=[]).json()
    assert plan.plan.weeks[0].sessions[entry["match"]["session_index"]].modality == "run"


def test_summary_line():
    entry = logbook.ManualActivity(
        id="a", plan_id="p", date=date(2026, 11, 3), modality="strength", title="x", duration_min=35, distance_km=0,
        rpe=7, note="", details=[Step(**s) for s in EXERCISES * 3], match=logbook.Match(),
    )
    assert logbook.summary_line(entry) == "Strength 35 min, RPE 7, 6 exercises"


def test_entries_list_alongside_files_without_a_review(client):
    log(client)
    item = client.get("/api/activities", params={"plan_id": "plan"}).json()[0]
    assert item["manual"] is True and item["review"] is None
    assert item["summary"] == "Strength 35 min, RPE 7, 2 exercises"


def test_counts_in_weekly_volume_and_load(client, plan):
    log(client)
    result = metrics.compute(storage.load_plan("plan"))
    week = result.weeks[0]
    assert (week.completed_strength, week.planned_strength) == (1, 2)
    assert week.completed_sessions == 1 and week.actual_km == 0
    tuesday = next(d for d in result.days if d.date.isoformat() == TUESDAY)
    assert tuesday.load == pytest.approx(35 * 0.5)  # strength factor


def test_cycling_log_counts_as_cross_minutes(client):
    log(client, date="2026-11-04", modality="cross", duration_min=45, distance_km=20, details=[])
    week = metrics.compute(storage.load_plan("plan")).weeks[0]
    assert week.actual_cross_min == 45 and week.planned_cross_min == 45 and week.actual_km == 0


def test_manual_run_counts_as_km(client):
    log(client, modality="run", duration_min=45, distance_km=8, details=[])
    result = metrics.compute(storage.load_plan("plan"))
    assert result.weeks[0].actual_km == 8


def test_progress_table_includes_the_log(client):
    log(client, modality="run", distance_km=8, details=[])
    row = progress.planned_vs_actual(storage.load_plan("plan"))["weeks"][0]
    assert row["actual_km"] == 8 and row["sessions_completed"] == 1


def test_edit_rematches_on_a_date_change(client):
    entry = log(client).json()
    moved = client.patch(f"/api/manual/{entry['id']}", json={"date": "2026-11-05", "note": "moved"}).json()
    assert moved["match"]["planned_date"] == "2026-11-05" and moved["note"] == "moved"


def test_edit_can_set_the_match_by_hand(client):
    entry = log(client, date="2026-11-02").json()
    edited = client.patch(f"/api/manual/{entry['id']}", json={"match": {"week_number": 1, "session_index": 1}}).json()
    assert edited["match"]["planned_date"] == TUESDAY
    bad = client.patch(f"/api/manual/{entry['id']}", json={"match": {"week_number": 1, "session_index": 99}})
    assert bad.status_code == 409


def test_delete_removes_it_and_updates_metrics(client):
    entry = log(client).json()
    assert client.delete(f"/api/manual/{entry['id']}").status_code == 204
    assert client.get("/api/activities", params={"plan_id": "plan"}).json() == []
    assert metrics.compute(storage.load_plan("plan")).weeks[0].completed_strength == 0
    assert client.delete(f"/api/manual/{entry['id']}").status_code == 404


def test_every_save_refreshes_the_cache(client):
    log(client)
    cache = (storage.plan_dir("plan") / "metrics_cache.json").read_text()
    assert '"completed_strength": 1' in cache


def test_null_in_an_edit_changes_nothing(client):
    entry = log(client, note="kept").json()
    response = client.patch(f"/api/manual/{entry['id']}", json={"note": None, "rpe": 3})
    assert response.json()["note"] == "kept" and response.json()["rpe"] == 3
    assert client.get("/api/activities", params={"plan_id": "plan"}).status_code == 200


def test_ids_are_unique_on_one_day(client):
    assert log(client).json()["id"] != log(client).json()["id"]


def test_future_dates_are_refused(client):
    assert log(client, date="2026-11-12").status_code == 409


@pytest.mark.parametrize("fields", [{"rpe": 11}, {"duration_min": -1}, {"distance_km": -1}, {"modality": "yoga"}])
def test_bad_fields_are_422(client, fields):
    assert log(client, **fields).status_code == 422


def test_only_the_active_plan_takes_entries(client):
    entry = log(client).json()
    other = storage.load_plan("plan")
    other.meta.id, other.meta.status = "next", "draft"
    storage.save_plan(other)
    drafted = client.post("/api/plans/next/manual", json={"date": TUESDAY, "modality": "run", "duration_min": 5}).json()
    assert drafted["plan_id"] == "" and drafted["match"]["session_index"] == -1
    assert client.post("/api/plans/nope/manual", json={"date": TUESDAY, "modality": "run", "duration_min": 5}).status_code == 404
    lifecycle.accept("next")
    assert client.patch(f"/api/manual/{entry['id']}", json={"rpe": 3}).status_code == 409
    assert client.delete(f"/api/manual/{entry['id']}").status_code == 409
    assert client.get("/api/activities", params={"plan_id": "plan"}).json()[0]["id"] == entry["id"]


def test_adaptation_sees_manual_entries(client, plan):
    log(client, note="glutes sore after this")
    _, fake = propose(plan, {"recommendation": "no_change", "rationale": "ok", "changes": [], "weeks": []})
    assert "glutes sore after this" in fake.prompts[0]
    assert "Training metrics" in fake.prompts[0]


def test_matches_survive_a_new_plan_version(client, plan):
    entry = log(client).json()
    current = storage.load_plan("plan")
    current.plan.weeks[0].sessions.reverse()
    adapt.replace_plan(current)
    index = storage.load_manual(entry["id"]).match.session_index
    assert current.plan.weeks[0].sessions[index].title == "Strength A"
