from datetime import date, timedelta
from pathlib import Path

import pytest

from app import activities, lifecycle, metrics, storage
from app.metrics import Entry, fitness_series, trimp
from app.models import Match, PlanFile

SAMPLES = Path("examples/activities")
TODAY = date(2026, 11, 11)


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    plan_file.request.hr_max, plan_file.request.hr_rest = 190, 50
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


def add_run(plan_file, name):
    run = activities.parse_file(name, (SAMPLES / name).read_bytes(), "plan")
    run.match = activities.match_activity(run, plan_file)
    storage.save_activity(run)
    return run


def test_constant_load_converges_to_100():
    ctl, _, _ = fitness_series([100] * 126, 0)[-1]
    assert ctl == pytest.approx(100, rel=0.05)


def test_ewma_by_hand():
    series = fitness_series([100, 0], 42)
    # Day 1: ctl 42 + (100-42)/42, atl 100/7, tsb from yesterday's 42 - 0.
    assert series[0] == pytest.approx((43.381, 14.286, 42), abs=0.001)
    # Day 2: tsb is day 1's ctl - atl.
    assert series[1][2] == pytest.approx(43.381 - 14.286, abs=0.001)
    assert series[1][1] == pytest.approx(14.286 * 6 / 7, abs=0.001)


def test_trimp_at_hrr_0_7():
    assert trimp(60, 148, 190, 50) == pytest.approx(103, abs=2)  # HRr = 98 / 140 = 0.7


def test_trimp_clamps_hrr():
    assert trimp(60, 40, 190, 50) == 0
    assert trimp(60, 250, 190, 50) == trimp(60, 190, 190, 50)


def test_load_without_hr_uses_the_type_factor():
    entry = Entry("a", TODAY, "run", 60, 10, 0, Match())
    assert metrics.entry_load(entry, "tempo", 190, 55) == pytest.approx(66)
    assert metrics.entry_load(entry, "strength", 190, 55) == pytest.approx(30)
    assert metrics.entry_load(entry, "cross_training", 190, 55) == pytest.approx(36)
    assert metrics.entry_load(entry, "unplanned", 190, 55) == pytest.approx(45)


@pytest.mark.parametrize(
    "fields,expected",
    [({"hr_max": 200, "hr_rest": 45}, (200, 45)), ({"age": 40}, (180, 55)), ({}, (190, 55))],
)
def test_hr_defaults(plan, fields, expected):
    request = plan.request.model_copy(update={"hr_max": 0, "hr_rest": 0} | fields)
    assert metrics.heart_rates(request) == pytest.approx(expected)


def test_weekly_km_match_hand_computed_sums(plan):
    tempo = add_run(plan, "2026-11-03_tempo.fit")
    easy = add_run(plan, "2026-11-05_easy.gpx")
    shakeout = add_run(plan, "2026-11-06_shakeout_unplanned.gpx")
    bad = add_run(plan, "2026-11-10_tempo_bad.fit")

    result = metrics.compute(plan, TODAY)
    first, second, third = result.weeks[:3]
    assert first.planned_km == 42 and second.planned_km == 45
    assert first.actual_km == pytest.approx(tempo.distance_km + easy.distance_km + shakeout.distance_km, abs=0.01)
    assert second.actual_km == bad.distance_km
    assert third.actual_km == 0
    assert (first.planned_sessions, first.completed_sessions) == (4, 2)
    assert len(result.weeks) == 10


def test_days_run_from_plan_start_to_today(plan):
    result = metrics.compute(plan, TODAY)
    assert result.days[0].date == date(2026, 11, 2) and result.days[-1].date == TODAY
    assert len(result.days) == 10 and result.as_of == TODAY


def old_run(day):
    run = activities.parse_file("a.gpx", (SAMPLES / "2026-11-05_easy.gpx").read_bytes(), "plan")
    start = run.start_time.replace(year=day.year, month=day.month, day=day.day)
    return run.model_copy(update={"id": f"old-{day}", "date": day, "start_time": start})


def test_runs_before_the_plan_do_not_decay_the_fitness_seed(plan):
    storage.save_activity(old_run(date(2026, 6, 1)))
    storage.save_activity(old_run(date(2017, 6, 12)))
    result = metrics.compute(plan, TODAY)
    assert result.days[0].date == date(2026, 11, 2) and len(result.days) == 10
    assert result.days[0].ctl == pytest.approx(44 + (result.days[0].load - 44) / 42, abs=0.1)
    assert result.current.ctl == pytest.approx(44 * (41 / 42) ** 10, abs=0.1)  # ten idle days, not 160


def test_before_the_plan_starts_there_are_no_days_but_no_crash(plan):
    result = metrics.compute(plan, date(2026, 10, 31))
    assert result.days == []
    assert result.current.ctl == pytest.approx(44.0) and result.current.atl == 0


def test_day_load_and_km_sum_same_day_entries(plan):
    run = add_run(plan, "2026-11-05_easy.gpx")
    second = run.model_copy(update={"id": "second"})
    storage.save_activity(second)
    result = metrics.compute(plan, TODAY)
    day = next(d for d in result.days if d.date == run.date)
    assert day.km == pytest.approx(2 * run.distance_km, abs=0.01)
    assert day.load == pytest.approx(2 * trimp(run.moving_time_s / 60, run.avg_hr, 190, 50), abs=0.2)


def test_snapshot_matches_the_last_day(plan):
    add_run(plan, "2026-11-03_tempo.fit")
    add_run(plan, "2026-11-05_easy.gpx")
    result = metrics.compute(plan, TODAY + timedelta(days=3))
    last = result.days[-1]
    assert (result.current.ctl, result.current.atl, result.current.tsb) == (last.ctl, last.atl, last.tsb)
    assert result.current.ctl_7d_change == pytest.approx(last.ctl - result.days[-8].ctl, abs=0.05)


def test_cycling_is_load_but_not_running_km(plan):
    run = add_run(plan, "2026-11-05_easy.gpx")
    run.modality = "cross"
    storage.save_activity(run)
    result = metrics.compute(plan, TODAY)
    day = next(d for d in result.days if d.date == run.date)
    assert day.km == 0 and day.load > 0
    assert result.weeks[0].actual_km == 0 and result.weeks[0].actual_cross_min == pytest.approx(run.moving_time_s / 60, abs=0.1)


def test_refresh_writes_the_cache(plan):
    result = metrics.refresh(plan, TODAY)
    assert (storage.plan_dir("plan") / "metrics_cache.json").read_text() == result.model_dump_json(indent=2)


def test_strength_and_cross_counts_for_the_chart_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    active = lifecycle.accept("plan")
    ride = activities.parse_file("a.fit", (SAMPLES / "cross" / "2026-11-04_ride.fit").read_bytes(), "plan")
    ride.match = activities.match_activity(ride, active)
    storage.save_activity(ride)
    week = metrics.compute(active, TODAY).weeks[0]
    assert (week.completed_cross, week.planned_cross) == (1, 1)
    assert (week.completed_strength, week.planned_strength) == (0, 2)


def test_equal_heart_rates_cannot_divide_by_zero(plan):
    request = plan.request.model_copy(update={"hr_max": 0, "hr_rest": 150, "age": 120})
    hr_max, hr_rest = metrics.heart_rates(request)
    assert hr_max > hr_rest
