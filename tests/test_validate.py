"""Each rule, triggered by a mutated copy of the sample plan."""
from datetime import date, timedelta

import pytest

from app import plan, validate
from app.models import Change, PlanDoc
from tests.conftest import TODAY

TODAY_DATE = date.fromisoformat(TODAY)


@pytest.fixture
def doc(demo_ws) -> PlanDoc:
    return plan.load()


def copy(doc: PlanDoc) -> PlanDoc:
    return doc.model_copy(deep=True)


def test_the_sample_plan_has_no_fact_errors(doc):
    assert validate.facts(doc.plan, doc.request) == []


def test_two_runs_on_one_day_is_an_error(doc):
    week = doc.plan.weeks[5]
    runs = [s for s in week.sessions if s.modality == "run"]
    runs[1].day, runs[1].date = runs[0].day, runs[0].date
    assert any("more than one run on the same day" in e for e in validate.facts(doc.plan, doc.request))


def test_a_skipped_run_does_not_count_as_a_second_run(doc):
    week = doc.plan.weeks[5]
    runs = [s for s in week.sessions if s.modality == "run"]
    runs[1].day, runs[1].date, runs[1].status = runs[0].day, runs[0].date, "skipped"
    assert not any("same day" in e for e in validate.facts(doc.plan, doc.request))


def test_type_and_modality_must_agree(doc):
    doc.plan.weeks[5].sessions[0].modality = "strength"
    assert any("must have modality run" in e for e in validate.facts(doc.plan, doc.request))


def test_the_race_must_be_on_the_race_date_and_last(doc):
    race = next(s for s in doc.plan.sessions() if s.type == "race")
    race.date -= timedelta(days=1)
    assert any("must be on the race date" in e for e in validate.facts(doc.plan, doc.request))


def test_nothing_after_the_race(doc):
    last = doc.plan.weeks[-1]
    extra = last.sessions[0].model_copy(update={"id": "extra", "date": doc.request.race_date + timedelta(days=1)})
    last.sessions.append(extra)
    assert any("after the race" in e for e in validate.facts(doc.plan, doc.request))


def test_duplicate_ids_are_an_error(doc):
    doc.plan.weeks[5].sessions[1].id = doc.plan.weeks[5].sessions[0].id
    assert any("is used 2 times" in e for e in validate.facts(doc.plan, doc.request))


def test_constraints_name_the_athletes_own_limits(doc):
    week = doc.plan.weeks[5]
    run = next(s for s in week.sessions if s.modality == "run")
    run.day = "Mon"
    assert any("not one of your usual training days" in w for w in validate.constraints(doc.plan, doc.request))


def test_a_new_plan_is_held_to_the_request_strictly(doc):
    run = next(s for s in doc.plan.weeks[5].sessions if s.modality == "run")
    run.day, run.date = "Mon", doc.plan.weeks[5].start_date
    errors, _ = validate.new_plan(doc, len(doc.plan.weeks))
    assert any("usual training days" in e for e in errors)
    errors, _ = validate.new_plan(plan.load(), 12)
    assert any("Expected 12 weeks" in e for e in errors)


def test_a_change_only_warns_about_the_athletes_limits(doc):
    new = copy(doc)
    run = next(s for s in new.plan.weeks[5].sessions if s.type == "easy")
    run.day, run.date = "Mon", new.plan.weeks[5].start_date
    errors, warnings = validate.change(doc, new, [], TODAY_DATE)
    assert errors == []
    assert any("usual training days" in w for w in warnings)


def test_what_already_happened_cannot_change_except_its_status(doc):
    new = copy(doc)
    past = new.plan.weeks[0].sessions[0]
    past.distance_km += 2
    errors, _ = validate.change(doc, new, [], TODAY_DATE)
    assert any("already happened" in e for e in errors)
    new = copy(doc)
    new.plan.weeks[0].sessions[0].status = "skipped"
    new.plan.weeks[0].sessions[0].edit_note = "Was ill"
    assert validate.change(doc, new, [Change(week=1, day=new.plan.weeks[0].sessions[0].day, summary="was ill")], TODAY_DATE)[0] == []


def test_a_session_matched_to_an_activity_is_frozen_even_today(doc):
    today_session = next(s for s in doc.plan.sessions() if s.date == TODAY_DATE)
    new = copy(doc)
    new.plan.session(today_session.id).duration_min += 15
    assert validate.change(doc, new, [], TODAY_DATE)[0] == []
    errors, _ = validate.change(doc, new, [], TODAY_DATE, done={today_session.id})
    assert any("already happened" in e for e in errors)


def test_a_session_cannot_move_into_the_past(doc):
    new = copy(doc)
    future = next(s for s in new.plan.sessions() if s.date > TODAY_DATE and s.type == "easy")
    future.date = TODAY_DATE - timedelta(days=1)
    errors, _ = validate.change(doc, new, [], TODAY_DATE)
    assert any("that day has passed" in e for e in errors)


def test_the_race_cannot_change(doc):
    new = copy(doc)
    next(s for s in new.plan.sessions() if s.type == "race").title = "Fun run"
    assert "The race session must stay exactly as it is" in validate.change(doc, new, [], TODAY_DATE)[0]


def test_a_removed_session_needs_its_own_change_entry(doc):
    new = copy(doc)
    week = new.plan.weeks[5]
    gone = next(s for s in week.sessions if s.type == "intervals")
    week.sessions.remove(gone)
    errors, _ = validate.change(doc, new, [], TODAY_DATE)
    assert any("without its own entry" in e for e in errors)
    explained = [Change(week=week.week_number, day=gone.day, summary="dropped: calf")]
    assert validate.change(doc, new, explained, TODAY_DATE)[0] == []


def test_an_imperfect_plan_does_not_block_a_later_change(doc):
    old = copy(doc)
    runs = [s for s in old.plan.weeks[6].sessions if s.modality == "run"]
    runs[1].day, runs[1].date = runs[0].day, runs[0].date  # already wrong before the change
    new = copy(old)
    next(s for s in new.plan.weeks[5].sessions if s.type == "easy").distance_km += 1
    assert validate.change(old, new, [], TODAY_DATE)[0] == []


def test_judgments_flag_back_to_back_hard_days_and_volume_jumps(doc):
    week = doc.plan.weeks[5]
    hard = [s for s in week.sessions if s.type in ("tempo", "intervals")]
    hard[1].date = hard[0].date + timedelta(days=1)
    week.planned_km = doc.plan.weeks[4].planned_km * 1.3
    found = validate.judgments(doc.plan)
    assert any("consecutive days" in w for w in found)
    assert any("rises more than 15%" in w for w in found)
