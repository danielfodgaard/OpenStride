from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, lifecycle, main, progress, storage
from app.models import PlanFile, ReviewFile, ReviewMeta, RunReview, Usage
from test_generator import FakeClient, reply

SAMPLES = Path("examples/activities")
SUMMARY = {"headline": "Solid first week", "on_track": "on_track", "observations": ["3 of 4 sessions"], "suggestions": ["Sleep more"]}


def add_run(name, completion=None, model="claude-haiku-5-5"):
    plan_file = storage.load_plan("plan")
    run = activities.parse_file(name, (SAMPLES / name).read_bytes(), "plan")
    run.match = activities.match_activity(run, plan_file)
    storage.save_activity(run)
    if completion:
        storage.save_review(ReviewFile(
            activity_id=run.id,
            review=RunReview(completion=completion, headline="x", observations=[], flags=[], effort="as_planned"),
            meta=ReviewMeta(model=model, created_at="now", usage=Usage(input_tokens=1000, output_tokens=100)),
        ))
    return run


@pytest.fixture(autouse=True)
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    plan_file.meta.usage = Usage(input_tokens=10000, output_tokens=5000)
    plan_file.meta.model = "claude-sonnet-5-5"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


def test_table_matches_hand_computed_numbers(plan):
    add_run("2026-11-03_tempo.fit", "as_planned")       # 8.000 km, week 1, planned
    add_run("2026-11-05_easy.gpx", "partly")            # 8.989 km, week 1, planned
    add_run("2026-11-06_shakeout_unplanned.gpx", "unplanned")  # 3.003 km, week 1, a rest day
    add_run("2026-11-10_tempo_bad.fit", "missed_targets")      # 6.000 km, week 2, planned

    table = progress.planned_vs_actual(plan)
    first, second, third = table["weeks"][:3]
    distances = [a.distance_km for a in storage.list_activities("plan")]

    assert first["planned_km"] == 42 and first["sessions_planned"] == 4
    assert first["actual_km"] == pytest.approx(sum(distances[:3]), abs=0.01)
    assert first["sessions_completed"] == 2  # the unplanned run doesn't complete a session
    assert (first["as_planned"], first["partly"], first["missed_targets"], first["unplanned"]) == (1, 1, 0, 1)

    assert second["planned_km"] == 45 and second["actual_km"] == 6.0
    assert second["sessions_completed"] == 1 and second["missed_targets"] == 1

    assert third["actual_km"] == 0 and third["sessions_completed"] == 0

    totals = table["totals"]
    assert totals["planned_km"] == pytest.approx(sum(w.planned_km for w in plan.plan.weeks), abs=0.01)
    assert totals["sessions_completed"] == 3
    assert totals["sessions_planned"] == 40


def test_two_runs_for_one_session_complete_it_once(plan):
    first = add_run("2026-11-05_easy.gpx")
    second = first.model_copy(update={"id": "second-part"})
    storage.save_activity(second)
    assert progress.planned_vs_actual(plan)["weeks"][0]["sessions_completed"] == 1


def test_manually_matched_run_counts_in_the_planned_week(plan):
    run = add_run("2026-11-10_tempo_bad.fit")  # Tue of week 2
    run.match.week_number, run.match.session_index = 1, 3  # counted for week 1's long run
    run.match.planned_date = "2026-11-08"
    storage.save_activity(run)
    weeks = progress.planned_vs_actual(plan)["weeks"]
    assert weeks[0]["actual_km"] == 6.0 and weeks[1]["actual_km"] == 0


def test_summary_is_saved_and_gets_full_context(plan):
    add_run("2026-11-05_easy.gpx", "as_planned")
    fake = FakeClient(reply(SUMMARY))
    result = progress.summarise("plan", fake, date(2026, 11, 11))

    assert result.summary.on_track == "on_track"
    assert progress.latest_summary("plan").summary.headline == "Solid first week"
    prompt = fake.prompts[0]
    assert "Planned vs actual:" in prompt and "Today: 2026-11-11" in prompt and "2026-11-05_easy.gpx" in prompt


def test_summary_never_changes_the_plan(plan):
    before = storage.load_plan("plan")
    progress.summarise("plan", FakeClient(reply(SUMMARY)), date(2026, 11, 11))
    assert storage.load_plan("plan") == before


def test_usage_adds_up_everything_for_the_plan(plan):
    add_run("2026-11-05_easy.gpx", "as_planned")
    progress.summarise("plan", FakeClient(reply(SUMMARY)), date(2026, 11, 11))  # sonnet: 100 in, 200 out
    totals = progress.usage_totals("plan")

    assert totals["input_tokens"] == 10000 + 1000 + 100
    assert totals["output_tokens"] == 5000 + 100 + 200
    # Sonnet 10,100 in + 5,200 out; Haiku 1,000 in + 100 out.
    expected = (10100 * 2 + 5200 * 10 + 1000 * 0.10 + 100 * 0.50) / 1_000_000
    assert totals["cost_usd"] == pytest.approx(expected, abs=0.0001)
    assert totals["models"] == ["claude-haiku-5-5", "claude-sonnet-5-5"]


def test_unknown_model_is_reported_not_priced(plan):
    add_run("2026-11-05_easy.gpx", "as_planned", model="claude-new")
    assert progress.usage_totals("plan")["unpriced_models"] == ["claude-new"]


def test_progress_is_for_the_active_plan_only(plan):
    client = TestClient(main.app)
    assert client.get("/api/plans/plan/progress").status_code == 200
    other = storage.load_plan("plan")
    other.meta.id, other.meta.status = "next", "draft"
    storage.save_plan(other)
    assert client.get("/api/plans/next/progress").status_code == 409
    assert client.post("/api/plans/next/progress/summary").status_code == 409
    lifecycle.accept("next")
    assert client.get("/api/plans/plan/progress").status_code == 409
    assert client.get("/api/plans/plan/usage").status_code == 200
