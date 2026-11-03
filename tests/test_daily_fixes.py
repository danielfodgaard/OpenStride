import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, config, lifecycle, main, metrics, progress, storage
from app.models import PlanFile
from app.prompts import adapt, chat, review as review_prompt

NOT_DUE = "Sessions dated after today are not due yet. Never count them as missed or behind."
SAMPLES = Path("examples/activities")
RUN = "2026-11-03_tue_easy_easy-run.fit"


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


def add_run(plan_file):
    run = activities.parse_file(RUN, (SAMPLES / RUN).read_bytes(), "plan")
    run.match = activities.match_activity(run, plan_file)
    storage.save_activity(run)
    return run


def test_next_up_skips_a_session_done_ahead_of_its_date(plan):
    run = add_run(plan)
    before_start = date(2026, 10, 31)
    status = progress.plan_status(storage.load_plan("plan"), before_start)
    assert run.match.session_index >= 0
    assert status["next"]["index"] != run.match.session_index and not status["next"]["done"]
    assert status["today_done"] is None
    assert status["km_actual"] == pytest.approx(run.distance_km, abs=0.1)
    assert status["sessions_completed"] == 1


def test_chart_and_table_agree_for_the_demo(tmp_path, monkeypatch):
    shutil.copytree("examples/demo/data", tmp_path / "data")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    plan_file = lifecycle.active_plan()
    chart = metrics.compute(plan_file, date(2026, 11, 25)).weeks
    table = progress.planned_vs_actual(plan_file)["weeks"]
    assert [(w.actual_km, w.completed_sessions) for w in chart] == [(r["actual_km"], r["sessions_completed"]) for r in table]


def test_a_run_ahead_of_today_counts_in_its_week_and_extends_fitness(plan):
    run = add_run(plan)
    result = metrics.compute(storage.load_plan("plan"), date(2026, 10, 31))
    assert result.weeks[0].actual_km == pytest.approx(run.distance_km, abs=0.01)
    assert result.days[-1].date == run.date
    assert result.days[-1].load > 0


def test_form_starts_at_zero(plan):
    first_day = plan.plan.weeks[0].start_date
    result = metrics.compute(plan, first_day)
    assert result.days[0].tsb == 0 and result.current.tsb == 0
    assert metrics.compute(plan, date(2026, 10, 31)).current.tsb == 0  # before the plan starts


def test_every_coaching_prompt_has_the_not_due_rule():
    for module in (review_prompt, adapt, chat):
        assert NOT_DUE in module.SYSTEM_PROMPT


def test_the_review_prompt_has_today_and_the_rule(plan):
    run = add_run(plan)
    prompt = review_prompt.build_user_prompt(run, plan, [], [], "{}", date(2026, 10, 31), "")
    assert "Today: 2026-10-31" in prompt


def test_future_import_warns_on_the_result_and_the_workout_page(plan, monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-10-31")
    monkeypatch.setattr(activities.review, "review_activity", lambda activity: None)
    with TestClient(main.app) as client:
        result = client.post("/api/activities", files={"files": (RUN, (SAMPLES / RUN).read_bytes())}).json()[0]
        assert result["status"] == "imported" and result["warnings"] == [activities.FUTURE_WARNING]
        assert client.get(f"/api/activities/{result['activity']['id']}").json()["warnings"] == [activities.FUTURE_WARNING]


def test_a_past_import_has_no_warning(plan, monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-12-01")
    assert activities.date_warnings(add_run(plan)) == []


def test_garmin_routes_with_mode_off(plan):
    with TestClient(main.app) as client:
        assert client.get("/api/garmin/status").json()["sync"] == "off"
        assert client.get("/api/plans/plan/sessions/1/0/workout.fit").status_code == 200  # needs no Garmin
        responses = [
            client.post("/api/garmin/sync"),
            client.post("/api/plans/plan/sessions/1/0/send-to-garmin"),
            client.post("/api/plans/plan/weeks/1/send-to-garmin"),
        ]
    assert {r.status_code for r in responses} == {501}
    assert all(r.json() == {"error": "garmin_not_configured"} for r in responses)


def test_settings_defaults_and_bad_mode(monkeypatch):
    for name in ("OPENSTRIDE_HOST", "OPENSTRIDE_PORT", "OPENSTRIDE_PASSWORD", "GARMIN_MODE", "GARMIN_SYNC", "GARMIN_PUSH", "GARMIN_SYNC_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    settings = config.load()
    assert (settings.host, settings.port, settings.password) == ("127.0.0.1", 8000, "")
    assert (settings.garmin_sync, settings.garmin_push, settings.garmin_sync_minutes) == ("off", "off", 0)
    monkeypatch.setenv("GARMIN_MODE", "strava")
    with pytest.raises(ValueError):
        config.load()


def test_a_batch_is_saved_before_any_review_runs(plan, monkeypatch):
    names = (RUN, "2026-11-05_thu_easy_easy-run.fit")
    seen = []
    monkeypatch.setattr(activities.review, "review_activity", lambda activity: seen.append(len(storage.list_activities("plan"))))
    activities.import_files([(name, (SAMPLES / name).read_bytes()) for name in names], "", 0)
    assert seen == [2, 2]
