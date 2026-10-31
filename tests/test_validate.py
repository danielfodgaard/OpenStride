import json
from pathlib import Path

import pytest

from app.models import PlanFile, SavedRequest, Session, TrainingPlan
from app.validate import soft_warnings, validate_plan

SAMPLE = json.loads(Path("examples/sample_plan.json").read_text())
N_WEEKS = len(SAMPLE["plan"]["weeks"])


@pytest.fixture
def plan():
    return TrainingPlan.model_validate(SAMPLE["plan"])


def errors_for(plan, **request_changes):
    request = SavedRequest.model_validate(SAMPLE["request"] | request_changes)
    return validate_plan(plan, request, N_WEEKS)


def find(plan, week, day):
    return next(s for s in plan.weeks[week].sessions if s.day == day)


def test_sample_passes(plan):
    assert errors_for(plan) == []
    assert soft_warnings(plan) == []


def test_week_count(plan):
    plan.weeks.pop(0)
    assert any("numbered 1..10" in e for e in errors_for(plan))


def test_week_numbers(plan):
    plan.weeks[1].week_number = 5
    assert any("numbered 1..10" in e for e in errors_for(plan))


def test_unavailable_day(plan):
    find(plan, 0, "Sat").day = "Fri"
    assert any("not an available day" in e for e in errors_for(plan))


def test_too_many_sessions(plan):
    extra = find(plan, 0, "Sat").model_copy(update={"day": "Mon"})
    plan.weeks[0].sessions.append(extra)
    assert any("more than runs_per_week" in e for e in errors_for(plan))


def test_two_runs_on_one_day(plan):
    find(plan, 0, "Sat").day = "Thu"
    assert any("more than one run" in e for e in errors_for(plan))


def strength_session(day):
    return Session(
        day=day, type="strength", modality="strength", title="Strength", distance_km=0,
        duration_min=30, target_pace="", description="", steps=[],
    )


def test_strength_may_share_a_day_with_a_run(plan):
    plan.weeks[0].sessions.append(strength_session("Tue"))
    assert errors_for(plan, strength_sessions_per_week=1) == []


def test_strength_beyond_the_request_is_too_many(plan):
    plan.weeks[0].sessions.append(strength_session("Tue"))
    assert any("more than the 4 requested" in e for e in errors_for(plan))


def test_strength_is_exempt_from_the_hard_day_rule(plan):
    plan.weeks[4].sessions.append(strength_session("Wed"))
    plan.weeks[4].sessions.append(strength_session("Sat"))
    errors = errors_for(plan, strength_sessions_per_week=2, available_days=["Tue", "Wed", "Thu", "Sat", "Sun"])
    assert errors == []


def test_modality_must_match_the_type(plan):
    find(plan, 0, "Sat").modality = "cross"
    assert any("must have modality run" in e for e in errors_for(plan))


def test_long_run_on_wrong_day(plan):
    find(plan, 0, "Sun").day = "Fri"
    assert any("long run" in e for e in errors_for(plan, available_days=["Tue", "Thu", "Fri", "Sat", "Sun"]))


def test_consecutive_hard_sessions(plan):
    find(plan, 4, "Thu").day = "Wed"
    assert any("consecutive" in e for e in errors_for(plan, available_days=["Tue", "Wed", "Sat", "Sun"]))


def test_consecutive_hard_sessions_across_weeks(plan):
    find(plan, 1, "Tue").day = "Mon"
    assert any("consecutive" in e for e in errors_for(plan, available_days=["Mon", "Tue", "Thu", "Sat", "Sun"]))


def test_missing_race(plan):
    find(plan, 9, "Sun").type = "easy"
    assert any("exactly one race" in e for e in errors_for(plan))


def test_two_races_in_last_week(plan):
    find(plan, 9, "Sat").type = "race"
    assert any("exactly one race" in e for e in errors_for(plan))


def test_race_in_earlier_week(plan):
    find(plan, 3, "Sun").type = "race"
    assert any("exactly one race" in e for e in errors_for(plan))


def test_race_on_wrong_weekday(plan):
    assert any("must be on Sat" in e for e in errors_for(plan, race_date="2027-01-09"))


def test_race_may_fall_outside_available_days(plan):
    race = find(plan, 9, "Sun")
    race.day = "Fri"
    assert errors_for(plan, race_date="2027-01-08") == [
        "The last week has a session after the race on Fri"
    ]


def test_session_after_race(plan):
    race = find(plan, 9, "Sun")
    find(plan, 9, "Sat").type, race.type = "race", "easy"
    assert any("after the race" in e for e in errors_for(plan, race_date="2027-01-09"))


def test_llm_may_adjust_paces(plan):
    find(plan, 0, "Sat").target_pace = "5:00/km"
    find(plan, 0, "Tue").steps[0].target_pace = "6:30/km"
    assert errors_for(plan) == []


def test_negative_numbers(plan):
    plan.weeks[0].planned_km = -1
    find(plan, 0, "Sat").distance_km = -1
    find(plan, 0, "Tue").steps[0].duration_min = -1
    errors = errors_for(plan)
    assert sum("negative" in e for e in errors) == 3


def test_repeat_below_one(plan):
    find(plan, 0, "Tue").steps[0].repeat = 0
    assert any("repeat below 1" in e for e in errors_for(plan))


def test_volume_jump_is_a_soft_warning(plan):
    plan.weeks[1].planned_km = plan.weeks[0].planned_km * 1.2
    assert errors_for(plan) == []
    assert any("more than 15%" in w for w in soft_warnings(plan))


def test_strength_and_cycling_sample_is_valid():
    sample = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    assert validate_plan(sample.plan, sample.request, 10) == []
    assert soft_warnings(sample.plan) == []
    assert {s.modality for w in sample.plan.weeks for s in w.sessions} == {"run", "strength", "cross"}
