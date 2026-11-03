"""Edits the athlete makes by hand. No test here, and no edit, reaches Claude."""
import json
import threading
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, adapt, edits, generator, lifecycle, main, metrics, progress, storage
from app.garmin import delivery, provider
from app.models import AdaptationResult, ManualActivity, Match, PlanFile, TrainingPlan
from app.prompts import adapt as adapt_prompt, chat, review as review_prompt

SAMPLES = Path("examples/activities")
RUN = "2026-11-03_tue_easy_easy-run.fit"  # Tue 3 Nov, about 8 km
TODAY = date(2026, 11, 2)
TUE, THU, SAT, SUN = date(2026, 11, 3), date(2026, 11, 5), date(2026, 11, 7), date(2026, 11, 8)
# Week 1: Tue tempo 8 + strength, Wed cross, Thu easy 9 + strength, Sat easy 9, Sun long 16.
TEMPO, THU_EASY, SAT_EASY, LONG = {"week": 1, "index": 0}, {"week": 1, "index": 3}, {"week": 1, "index": 5}, {"week": 1, "index": 6}
STRENGTH = {"week": 1, "index": 1}


@pytest.fixture(autouse=True)
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENSTRIDE_TODAY", TODAY.isoformat())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used")
    monkeypatch.setattr(generator, "make_client", lambda: pytest.fail("an edit called the LLM"))
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def edit(kind, session=TEMPO, **fields):
    return edits.apply("plan", kind, session, **fields)


def at(number, index):
    return storage.load_plan("plan").plan.weeks[number - 1].sessions[index]


def find(day):
    return [
        ((w.week_number, i), s) for w in storage.load_plan("plan").plan.weeks for i, s in enumerate(w.sessions)
        if s.date == day
    ]


def add_run(match=None):
    run = activities.parse_file(RUN, (SAMPLES / RUN).read_bytes(), "plan")
    run.match = match or Match()
    storage.save_activity(run)
    return run


def matched_session(run):
    saved = storage.load_activity(run.id)
    return at(saved.match.week_number, saved.match.session_index)


def test_move_changes_date_and_day_and_records_where_from():
    result = edit("move", to_date=date(2026, 11, 4), note="work trip")
    moved = next(s for _, s in find(date(2026, 11, 4)) if s.type == "tempo")
    assert (moved.day, moved.moved_from, moved.edit_note) == ("Wed", TUE, "Moved from Tue: work trip")
    assert result["summary"].startswith("Moved Tue") and result["version"] == 2
    last = storage.load_plan("plan").meta.history[-1]
    assert last.origin == "manual" and last.summary == result["summary"]
    assert storage.load_version("plan", 2).meta.version == 2


def test_moving_back_clears_moved_from():
    edit("move", to_date=date(2026, 11, 4))
    index = next(i for i, s in enumerate(storage.load_plan("plan").plan.weeks[0].sessions) if s.moved_from)
    edit("move", {"week": 1, "index": index}, to_date=TUE)
    assert next(s for _, s in find(TUE) if s.type == "tempo").moved_from is None


def test_a_move_across_a_week_boundary_updates_both_weeks():
    edit("move", THU_EASY, to_date=date(2026, 11, 13))  # Fri of week 2
    plan_file = storage.load_plan("plan")
    first, second = plan_file.plan.weeks[:2]
    assert (first.planned_km, second.planned_km) == (33, 54)
    assert len(first.sessions) == 6 and len(second.sessions) == 8
    assert [s.date for s in second.sessions] == sorted(s.date for s in second.sessions)
    assert not [s for s in first.sessions if s.date >= date(2026, 11, 9)]


def test_swap_within_a_week_and_across_weeks():
    edit("swap", THU_EASY, other=SAT_EASY)
    assert next(s for _, s in find(THU) if s.type == "easy").moved_from == SAT
    edit("swap", LONG, other={"week": 2, "index": 0})
    plan_file = storage.load_plan("plan")
    assert any(s.type == "long" and s.date == date(2026, 11, 10) for s in plan_file.plan.weeks[1].sessions)
    assert any(s.type == "tempo" and s.date == SUN for s in plan_file.plan.weeks[0].sessions)


def test_a_week_two_away_is_refused_for_a_swap():
    with pytest.raises(lifecycle.Conflict, match="adjacent"):
        edit("swap", other={"week": 3, "index": 0})


def test_a_swap_needs_both_sessions_ahead():
    with pytest.raises(lifecycle.Conflict, match="today or later"):
        edits.apply("plan", "swap", TEMPO, other=THU_EASY, today=date(2026, 11, 4))


def test_skip_and_unskip_work_on_the_past_too():
    edits.apply("plan", "skip", TEMPO, note="sick", today=date(2026, 11, 5))
    assert (at(1, 0).status, at(1, 0).edit_note) == ("skipped", "Skipped: sick")
    edit("unskip")
    assert (at(1, 0).status, at(1, 0).edit_note) == ("planned", "")


@pytest.mark.parametrize("fields,text", [
    ({"to_date": date(2026, 11, 1)}, "past"),
    ({"to_date": date(2027, 1, 10)}, "race"),
    ({"to_date": date(2027, 1, 11)}, "race"),
    ({"to_date": THU}, "Swap"),
    ({}, "Pick a date"),
])
def test_moves_that_are_refused(fields, text):
    before = storage.load_plan("plan")
    with pytest.raises(lifecycle.Conflict, match=text):
        edit("move", **fields)
    assert storage.load_plan("plan") == before


@pytest.mark.parametrize("kind,fields", [("move", {"to_date": date(2026, 11, 4)}), ("skip", {}), ("swap", {"other": TEMPO})])
def test_the_race_can_not_be_touched(kind, fields):
    weeks = storage.load_plan("plan").plan.weeks
    index = next(i for i, s in enumerate(weeks[-1].sessions) if s.type == "race")
    with pytest.raises(lifecycle.Conflict, match="race"):
        edit(kind, {"week": weeks[-1].week_number, "index": index}, **fields)


def test_a_strength_session_can_share_a_day_with_a_run():
    edit("move", STRENGTH, to_date=SAT)
    assert any(s.modality == "strength" for _, s in find(SAT))


def test_a_skipped_run_leaves_its_day_free():
    edit("skip", THU_EASY)
    edit("move", to_date=THU)
    assert any(s.type == "tempo" for _, s in find(THU))


def test_unskip_is_refused_when_the_day_has_filled():
    edit("skip", THU_EASY)
    edit("move", to_date=THU)
    index = next(i for i, s in enumerate(storage.load_plan("plan").plan.weeks[0].sessions) if s.status == "skipped")
    with pytest.raises(lifecycle.Conflict, match="Swap"):
        edit("unskip", {"week": 1, "index": index})


def test_warnings_allow_the_edit():
    result = edit("move", to_date=date(2026, 11, 6))  # Fri: not an available day, next to nothing hard
    assert any("usual training days" in w for w in result["warnings"])
    index = next(i for i, s in enumerate(storage.load_plan("plan").plan.weeks[0].sessions) if s.type == "long")
    result = edit("move", {"week": 1, "index": index}, to_date=date(2026, 11, 4))
    assert any("long run moves" in w for w in result["warnings"])


def test_back_to_back_hard_runs_warn_and_volume_jumps_warn():
    assert any("consecutive" in w for w in edit("move", LONG, to_date=date(2026, 11, 4))["warnings"])
    assert any("rises more than 15%" in w for w in edit("move", THU_EASY, to_date=date(2026, 11, 13))["warnings"])


def test_a_done_session_can_not_be_moved_or_skipped():
    add_run(Match(planned_date="2026-11-03", week_number=1, session_index=0))
    with pytest.raises(lifecycle.Conflict, match="done"):
        edit("move", to_date=date(2026, 11, 4))
    with pytest.raises(lifecycle.Conflict, match="done"):
        edit("skip")


def test_matches_follow_a_session_whose_index_shifted():
    run = add_run(Match(planned_date="2026-11-03", week_number=1, session_index=0))
    edit("move", {"week": 2, "index": 0}, to_date=date(2026, 11, 2))  # into week 1, ahead of the tempo
    assert matched_session(run).type == "tempo"


def test_a_moved_session_picks_up_a_run_on_its_new_day():
    run = add_run()
    edit("skip")
    edit("move", THU_EASY, to_date=TUE)
    assert matched_session(run).type == "easy"


def test_it_was_this_session_moves_it_and_matches_the_run():
    run = add_run()
    result = edit("assign_activity", THU_EASY, activity_id=run.id)
    assert matched_session(run).date == TUE and matched_session(run).type == "easy"
    assert matched_session(run).moved_from == THU
    assert any("two runs" in w for w in result["warnings"])  # the tempo is still on Tue


def test_assign_survives_a_later_edit_on_a_day_with_two_runs():
    run = add_run()
    edit("assign_activity", THU_EASY, activity_id=run.id)
    edit("skip", SAT_EASY)
    assert matched_session(run).type == "easy"


@pytest.mark.parametrize("session,text", [
    ({"week": 2, "index": 3}, "within 6 days"),
    (STRENGTH, "planned run"),
])
def test_assign_refusals(session, text):
    run = add_run()
    with pytest.raises(lifecycle.Conflict, match=text):
        edit("assign_activity", session, activity_id=run.id)


def test_an_activity_already_matched_can_not_be_assigned():
    run = add_run(Match(planned_date="2026-11-03", week_number=1, session_index=0))
    with pytest.raises(lifecycle.Conflict, match="already"):
        edit("assign_activity", THU_EASY, activity_id=run.id)


def test_an_unattached_workout_joins_the_plan():
    run = add_run()
    run.plan_id = ""
    storage.save_activity(run)
    edit("replace", TEMPO, activity_id=run.id)
    assert storage.load_activity(run.id).plan_id == "plan"


def test_replace_marks_the_session_and_matches_the_run():
    run = add_run()
    edit("replace", TEMPO, activity_id=run.id, note="legs tired")
    assert at(1, 0).status == "replaced" and matched_session(run).type == "tempo"
    assert at(1, 0).edit_note == "Replaced: legs tired"


def test_replace_needs_a_session_on_that_day():
    run = add_run()
    with pytest.raises(lifecycle.Conflict, match="day of this workout"):
        edit("replace", THU_EASY, activity_id=run.id)


def test_change_rewrites_distance_pace_and_type_and_the_week_total():
    before = at(1, 0)
    result = edit("change", changes={"distance_km": 12, "target_pace": "5:30/km", "type": "easy", "title": "Easy run"}, note="legs heavy")
    changed = at(1, 0)
    assert (changed.distance_km, changed.target_pace, changed.type, changed.title) == (12, "5:30/km", "easy", "Easy run")
    assert changed.edit_note == f"Changed {before.distance_km:g} to 12 km, {before.target_pace} to 5:30/km, tempo to easy: legs heavy"
    assert result["summary"].startswith("Changed Tue tempo")
    assert storage.load_plan("plan").plan.weeks[0].planned_km == round(42 - before.distance_km + 12, 2)
    edits.undo("plan")
    assert at(1, 0) == before


def test_change_drops_the_steps_when_the_amount_changes_but_not_for_wording():
    assert at(1, 0).steps
    edit("change", changes={"description": "Take the long way round"})
    assert at(1, 0).steps and at(1, 0).edit_note == "Changed the wording"
    edit("change", changes={"duration_min": 50})
    assert at(1, 0).steps == []


@pytest.mark.parametrize("session, changes, text", [
    (TEMPO, {"distance_km": 8}, "Nothing changed"),
    (TEMPO, {"type": "strength"}, "can't become"),
    (STRENGTH, {"type": "easy"}, "can't become"),
    ({"week": 1, "index": 0}, {"distance_km": 5}, "today or later"),
])
def test_changes_that_are_refused(session, changes, text):
    with pytest.raises(lifecycle.Conflict, match=text):
        edits.apply("plan", "change", session, changes=changes, today=date(2026, 11, 4) if text == "today or later" else TODAY)


def test_undo_restores_exactly():
    run = add_run()
    original = storage.load_plan("plan")
    edit("assign_activity", THU_EASY, activity_id=run.id)
    restored = edits.undo("plan")["plan"]
    assert restored.plan == original.plan and storage.load_plan("plan").plan == original.plan
    assert storage.load_activity(run.id).match == Match()
    assert restored.meta.version == 3 and restored.meta.history[-1].origin == "undo"


def test_undo_puts_index_shifted_matches_back():
    run = add_run(Match(planned_date="2026-11-03", week_number=1, session_index=0))
    edit("move", {"week": 2, "index": 0}, to_date=date(2026, 11, 2))
    edits.undo("plan")
    assert storage.load_activity(run.id).match.session_index == 0


def test_undo_only_after_a_hand_edit():
    with pytest.raises(lifecycle.Conflict, match="undo"):
        edits.undo("plan")
    edit("skip")
    edits.undo("plan")
    with pytest.raises(lifecycle.Conflict, match="undo"):  # an undo isn't undone by pressing it again
        edits.undo("plan")


def test_old_plan_files_load_as_planned():
    raw = json.loads(Path("examples/sample_plan.json").read_text())
    assert "status" not in raw["plan"]["weeks"][0]["sessions"][0]
    session = PlanFile.model_validate(raw).plan.weeks[0].sessions[0]
    assert (session.status, session.moved_from, session.edit_note) == ("planned", None, "")
    assert PlanFile.model_validate(raw | {"meta": raw["meta"] | {"history": [
        {"version": 2, "proposal_id": "p", "accepted_at": "t", "rationale": "r"},
    ]}}).meta.history[0].origin == "proposal"


def test_the_llm_schema_has_not_changed():
    text = json.dumps(TrainingPlan.model_json_schema()) + json.dumps(AdaptationResult.model_json_schema())
    assert "moved_from" not in text and "edit_note" not in text


def test_the_api_needs_if_match_and_refuses_a_stale_plan(client):
    body = {"kind": "skip", "session": TEMPO}
    assert client.post("/api/plans/plan/edits", json=body).status_code == 428
    assert client.post("/api/plans/plan/undo").status_code == 428
    etag = client.get("/api/plans/plan").headers["etag"]
    response = client.post("/api/plans/plan/edits", json=body, headers={"If-Match": etag})
    assert response.status_code == 200 and response.json()["version"] == 2
    assert client.post("/api/plans/plan/edits", json=body, headers={"If-Match": etag}).json() == {"error": "plan_changed"}
    etag = client.get("/api/plans/plan").headers["etag"]
    assert client.post("/api/plans/plan/undo", headers={"If-Match": etag}).json()["meta"]["version"] == 3


def test_a_refused_edit_gives_409_with_the_reason(client):
    etag = client.get("/api/plans/plan").headers["etag"]
    body = {"kind": "move", "session": TEMPO, "to_date": "2026-11-05"}
    response = client.post("/api/plans/plan/edits", json=body, headers={"If-Match": etag})
    assert response.status_code == 409 and "Swap" in response.json()["detail"]


def test_two_edits_at_once_one_wins(client):
    etag = client.get("/api/plans/plan").headers["etag"]
    gate = threading.Barrier(2)
    codes = []

    def send(session):
        gate.wait()
        codes.append(client.post("/api/plans/plan/edits", json={"kind": "skip", "session": session}, headers={"If-Match": etag}).status_code)

    threads = [threading.Thread(target=send, args=(s,)) for s in (TEMPO, THU_EASY)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200, 409] and storage.load_plan("plan").meta.version == 2


def test_only_active_plans_can_be_edited():
    with pytest.raises(lifecycle.Conflict):
        plan_file = storage.load_plan("plan")
        plan_file.meta.status = "archived"
        storage.save_plan(plan_file)
        edit("skip")


def test_a_proposal_keeps_the_athletes_edits_in_its_weeks():
    edit("skip", SAT_EASY)
    plan_file = storage.load_plan("plan")
    from app.models import Week
    week = Week.model_validate(plan_file.plan.weeks[0].model_dump())
    merged = adapt.merge(plan_file, [week])
    assert merged.plan.weeks[0].sessions[5].status == "skipped"


# Phase 2: numbers worked out by hand from week 1: 42 km, 7 sessions, 2 strength.
def test_a_skipped_session_leaves_what_was_expected():
    edit("skip", THU_EASY)
    row = progress.planned_vs_actual(storage.load_plan("plan"))["weeks"][0]
    assert (row["planned_km"], row["sessions_planned"], row["sessions_skipped"]) == (33, 6, 1)


def test_a_skipped_strength_session_drops_from_the_strength_count():
    edit("skip", STRENGTH)
    assert metrics.compute(storage.load_plan("plan"), THU).weeks[0].planned_strength == 1


def test_skipping_is_never_missed_in_the_status():
    edit("skip", TEMPO)
    status = progress.plan_status(storage.load_plan("plan"), THU)
    assert status["sessions_planned"] == 4 and status["km_planned"] == 9  # Tue strength, Wed cross, Thu easy and strength


def test_next_up_passes_over_a_skipped_session():
    edit("skip", TEMPO)
    assert progress.plan_status(storage.load_plan("plan"), TUE)["next"]["type"] == "strength"


def test_a_run_on_a_skipped_session_stays_unplanned():
    edit("skip", TEMPO)
    plan_file = storage.load_plan("plan")
    run = activities.parse_file(RUN, (SAMPLES / RUN).read_bytes(), "plan")
    assert activities.match_activity(run, plan_file) == Match()


def test_a_replaced_session_counts_the_actual_km_as_done():
    run = add_run()
    edit("replace", TEMPO, activity_id=run.id)
    row = progress.planned_vs_actual(storage.load_plan("plan"))["weeks"][0]
    assert row["actual_km"] == pytest.approx(run.distance_km, abs=0.01)
    assert (row["sessions_completed"], row["sessions_replaced"], row["sessions_planned"]) == (1, 1, 7)


def test_a_replaced_session_loads_as_what_was_done():
    entry = ManualActivity(id="m", plan_id="plan", date=TUE, modality="run", title="Easy", duration_min=60, distance_km=10, rpe=0, note="", details=[], match=Match())
    storage.save_manual(entry)
    edit("replace", TEMPO, activity_id="m")
    plan_file = storage.load_plan("plan")
    done = next(e for e in metrics.entries("plan") if e.id == "m")
    assert metrics.session_type(done, plan_file) == "unplanned"  # easy-run load, not tempo's 1.1
    assert metrics.entry_load(done, "unplanned", 190, 55) == 45


def test_every_coaching_prompt_explains_the_edit_fields():
    for module in (review_prompt, adapt_prompt, chat):
        assert "edit_note" in module.SYSTEM_PROMPT and "skipped" in module.SYSTEM_PROMPT


def test_the_review_is_told_what_replaced_what():
    run = add_run()
    edit("replace", TEMPO, activity_id=run.id)
    prompt = review_prompt.build_user_prompt(storage.load_activity(run.id), storage.load_plan("plan"), [], [], "{}", TUE, "")
    assert f"Planned {at(1, 0).title}, did the run below" in prompt and '"status": "replaced"' in prompt


def test_the_plan_in_prompts_carries_the_edit_fields():
    edit("move", to_date=date(2026, 11, 4), note="work trip")
    plan_file = storage.load_plan("plan")
    prompt = adapt_prompt.build_user_prompt(plan_file, [], "{}", [1], TODAY, "", "")
    assert '"moved_from": "2026-11-03"' in prompt and "work trip" in prompt


# Phase 5, against a fake.
class FakePusher:
    def __init__(self):
        self.pushed, self.deleted = [], []

    def can_push_workouts(self):
        return True

    def push_workout(self, workout_json, day):
        self.pushed.append(day)
        return f"new-{len(self.pushed)}"

    def delete_workout(self, workout_id):
        self.deleted.append(workout_id)


@pytest.fixture
def garmin(monkeypatch):
    fake = FakePusher()
    monkeypatch.setattr(provider, "make_pusher", lambda: fake)
    monkeypatch.setattr(provider, "status", lambda: {"can_push_workouts": True})
    delivery.send_session(fake, storage.load_plan("plan"), 1, 0)
    fake.pushed.clear()
    return fake


def run_edit(kind, session=TEMPO, **fields):
    result = edit(kind, session, **fields)
    return delivery.follow_edit(result["plan"], **result["garmin"])


def test_moving_a_sent_session_reschedules_it(garmin):
    assert run_edit("move", to_date=date(2026, 11, 4)) == []
    assert garmin.pushed == [date(2026, 11, 4)] and garmin.deleted == ["new-1"]
    entry = next(iter(delivery.load_sent("plan").values()))
    assert entry["scheduled_date"] == "2026-11-04"


def test_a_move_across_weeks_rekeys_the_mapping(garmin):
    run_edit("move", to_date=date(2026, 11, 11))
    sent = delivery.load_sent("plan")
    assert len(sent) == 1 and next(iter(sent)).startswith("2/")


def test_skipping_a_sent_session_removes_it(garmin):
    assert run_edit("skip") == []
    assert garmin.deleted == ["new-1"] and delivery.load_sent("plan") == {}


def test_an_unsent_session_touches_nothing(garmin):
    run_edit("move", THU_EASY, to_date=date(2026, 11, 6))
    assert garmin.pushed == [] and garmin.deleted == []


def test_changing_a_sent_session_resends_it_and_undo_sends_the_old_one(garmin):
    assert run_edit("change", changes={"distance_km": 12}) == []
    assert garmin.pushed == [TUE] and garmin.deleted == ["new-1"]
    result = edits.undo("plan")
    delivery.follow_edit(result["plan"], **result["garmin"])
    assert garmin.pushed == [TUE, TUE] and len(garmin.deleted) == 2


def test_undo_moves_it_back_on_garmin(garmin):
    run_edit("move", to_date=date(2026, 11, 4))
    result = edits.undo("plan")
    delivery.follow_edit(result["plan"], **result["garmin"])
    assert garmin.pushed[-1] == TUE


def test_a_garmin_failure_never_undoes_the_edit(garmin):
    def boom(*args):
        raise provider.GarminError("Garmin said no.")

    garmin.push_workout = boom
    problems = run_edit("move", to_date=date(2026, 11, 4))
    assert "Garmin said no" in problems[0] and at(1, 0).type != "" and storage.load_plan("plan").meta.version == 2


def test_with_push_off_the_athlete_is_told(garmin, monkeypatch):
    monkeypatch.setattr(provider, "status", lambda: {"can_push_workouts": False})
    problems = run_edit("move", to_date=date(2026, 11, 4))
    assert ".fit" in problems[0] and garmin.pushed == []
    assert next(iter(delivery.load_sent("plan"))) in {"1/0", "1/1", "1/2"}  # the mapping still follows
