import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, adapt, lifecycle, main, storage
from app.models import AdaptationResult, PlanFile, Week
from test_generator import FakeClient, reply

TODAY = date(2026, 11, 11)  # Wednesday of week 2: Tuesday's session has passed


def plan_weeks(plan_file, numbers):
    """The weeks as the LLM would return them: no dates."""
    return [
        json.loads(Week.model_validate(week.model_dump(exclude={"start_date"})).model_dump_json())
        for week in plan_file.plan.weeks if week.week_number in numbers
    ]


def change_result(plan_file, numbers, edit=None):
    weeks = plan_weeks(plan_file, numbers)
    if edit:
        edit(weeks)
    return {
        "recommendation": "change",
        "rationale": "Calf niggle, so ease off.",
        "changes": [{"week_number": numbers[0], "day": "Thu", "summary": "Intervals to easy"}],
        "weeks": weeks,
    }


def soften_thursday(weeks):
    thursday = next(s for s in weeks[0]["sessions"] if s["day"] == "Thu")
    thursday.update(type="easy", title="Easy 6 km", distance_km=6, steps=[])


@pytest.fixture(autouse=True)
def active(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


def propose(plan_file, *results, scope="this_week", context=""):
    client = FakeClient(*[reply(r) for r in results])
    proposal = adapt.create_proposal("plan", scope, context, client, TODAY)
    return proposal, client


def good_proposal(active):
    return propose(active, change_result(active, [2], soften_thursday))[0]


def test_scope_starts_at_the_first_week_with_sessions_after_today(active):
    assert adapt.compute_scope_weeks(active, "this_week", TODAY) == [2]
    assert adapt.compute_scope_weeks(active, "next_2_weeks", TODAY) == [2, 3]
    assert adapt.compute_scope_weeks(active, "rest_of_plan", TODAY) == list(range(2, 11))
    # Sunday night: week 2 has nothing left.
    assert adapt.compute_scope_weeks(active, "this_week", date(2026, 11, 15)) == [3]
    assert adapt.compute_scope_weeks(active, "next_2_weeks", date(2027, 1, 5)) == [10]


def test_scope_after_the_race_is_a_conflict(active):
    with pytest.raises(lifecycle.Conflict):
        adapt.compute_scope_weeks(active, "this_week", date(2027, 1, 10))


def test_merge_only_touches_scope_weeks(active):
    result = AdaptationResult.model_validate(change_result(active, [2], soften_thursday))
    merged = adapt.merge(active, result.weeks)
    for old, new in zip(active.plan.weeks, merged.plan.weeks):
        if old.week_number != 2:
            assert old == new
    changed = next(s for s in merged.plan.weeks[1].sessions if s.day == "Thu")
    assert changed.type == "easy" and changed.date == date(2026, 11, 12)
    assert merged.plan.weeks[1].start_date == date(2026, 11, 9)


def test_context_reaches_the_model(active):
    _, client = propose(active, {"recommendation": "no_change", "rationale": "Fine", "changes": [], "weeks": []}, context="calf niggle")
    prompt = client.prompts[0]
    assert "calf niggle" in prompt and "Today: 2026-11-11" in prompt and "Scope weeks: [2]" in prompt


def test_no_change_is_a_valid_outcome_with_nothing_to_accept(active):
    proposal, _ = propose(active, {"recommendation": "no_change", "rationale": "One bad run.", "changes": [], "weeks": []})
    assert proposal.acceptable and proposal.result.recommendation == "no_change"
    with pytest.raises(lifecycle.Conflict):
        adapt.accept(proposal.proposal_id, TODAY)
    assert storage.load_plan("plan") == active


def test_validator_catches_out_of_scope_weeks(active):
    bad = change_result(active, [3])
    proposal, client = propose(active, bad, bad)
    assert not proposal.acceptable
    assert "Return exactly weeks [2], got [3]" in proposal.result.rationale
    assert "Fix these problems" in client.prompts[1]


def test_validator_catches_a_changed_past_session(active):
    def edit(weeks):
        next(s for s in weeks[0]["sessions"] if s["day"] == "Tue").update(title="Rewritten history")

    bad = change_result(active, [2], edit)
    proposal, _ = propose(active, bad, bad)
    assert not proposal.acceptable and "must stay exactly" in proposal.result.rationale


def test_validator_catches_a_moved_race(active):
    def edit(weeks):
        next(s for s in weeks[-1]["sessions"] if s["type"] == "race").update(distance_km=10)

    bad = change_result(active, list(range(2, 11)), edit)
    proposal, _ = propose(active, bad, bad, scope="rest_of_plan")
    assert not proposal.acceptable and "race session" in proposal.result.rationale


def test_validator_runs_the_v1_checks_on_the_merged_plan(active):
    def edit(weeks):
        weeks[0]["sessions"][-1]["day"] = "Mon"  # the long run

    bad = change_result(active, [2], edit)
    proposal, _ = propose(active, bad, bad)
    assert not proposal.acceptable and "not an available day" in proposal.result.rationale


def test_retry_can_fix_a_bad_proposal(active):
    proposal, _ = propose(active, change_result(active, [3]), change_result(active, [2], soften_thursday))
    assert proposal.acceptable and proposal.meta.usage.input_tokens == 200


def test_existing_problems_do_not_block_adaptation(active):
    active.plan.weeks[4].sessions[0].day = "Mon"  # out of scope, already invalid
    storage.save_plan(active)
    proposal, _ = propose(active, change_result(active, [2], soften_thursday))
    assert proposal.acceptable


def test_accept_creates_a_new_version(active):
    proposal = good_proposal(active)
    assert storage.load_plan("plan") == active  # nothing changes before accepting

    result = adapt.accept(proposal.proposal_id, TODAY)
    assert result.meta.version == 2 and result.meta.status == "active"
    assert result.meta.history[0].proposal_id == proposal.proposal_id
    assert storage.load_version("plan", 1) == active
    assert storage.load_version("plan", 2) == result
    assert storage.load_proposal(proposal.proposal_id).status == "accepted"
    assert next(s for s in storage.load_plan("plan").plan.weeks[1].sessions if s.day == "Thu").type == "easy"


def test_a_proposal_can_only_be_accepted_once(active):
    proposal = good_proposal(active)
    adapt.accept(proposal.proposal_id, TODAY)
    with pytest.raises(lifecycle.Conflict, match="already accepted"):
        adapt.accept(proposal.proposal_id, TODAY)


def test_reject_leaves_the_plan_untouched(active):
    proposal = good_proposal(active)
    assert adapt.reject(proposal.proposal_id).status == "rejected"
    assert storage.load_plan("plan") == active
    with pytest.raises(lifecycle.Conflict):
        adapt.accept(proposal.proposal_id, TODAY)


def test_stale_proposal_is_refused(active):
    first = good_proposal(active)
    second = good_proposal(active)
    adapt.accept(first.proposal_id, TODAY)
    with pytest.raises(lifecycle.Conflict, match="changed since"):
        adapt.accept(second.proposal_id, TODAY)


def test_accepting_after_the_changed_session_has_passed_is_refused(active):
    proposal = good_proposal(active)
    with pytest.raises(lifecycle.Conflict, match="out of date"):
        adapt.accept(proposal.proposal_id, date(2026, 11, 13))  # Thursday's change is now history
    assert storage.load_plan("plan") == active


def test_restore_creates_a_new_version(active):
    proposal = good_proposal(active)
    adapt.accept(proposal.proposal_id, TODAY)
    restored = adapt.restore("plan", 1)
    assert restored.meta.version == 3 and restored.meta.status == "active"
    assert restored.plan == active.plan
    assert [h.rationale for h in restored.meta.history][-1] == "Restored version 1"
    assert storage.load_version("plan", 2).plan != active.plan  # nothing is deleted


def test_restore_missing_version_is_404():
    with pytest.raises(storage.NotFound):
        adapt.restore("plan", 9)


def test_matches_follow_the_plan_when_sessions_are_reordered(active):
    activity = activities.parse_file("a.gpx", Path("examples/activities/2026-11-05_easy.gpx").read_bytes(), "plan")
    activity.match = activities.match_activity(activity, active)
    storage.save_activity(activity)
    assert activity.match.session_index == 1

    merged = active.model_copy(deep=True)
    merged.plan.weeks[0].sessions.reverse()
    activities.rematch(merged)
    assert storage.load_activity(activity.id).match.session_index == 2

    merged.plan.weeks[0].sessions = [s for s in merged.plan.weeks[0].sessions if s.day != "Thu"]
    activities.rematch(merged)
    assert storage.load_activity(activity.id).match.session_index == -1


def test_endpoints_refuse_non_active_plans():
    client = TestClient(main.app)
    proposal = good_proposal(storage.load_plan("plan"))
    other = storage.load_plan("plan")
    other.meta.id, other.meta.status = "next", "draft"
    storage.save_plan(other)

    assert client.post("/api/plans/next/proposals", json={"scope": "this_week"}).status_code == 409
    assert client.post("/api/plans/next/restore/1").status_code == 409

    lifecycle.accept("next")  # archives "plan"
    assert client.post(f"/api/proposals/{proposal.proposal_id}/accept").status_code == 409
    assert client.post(f"/api/proposals/{proposal.proposal_id}/reject").status_code == 409
    assert client.post("/api/plans/plan/restore/1").status_code == 409
    assert client.post("/api/plans/nope/proposals", json={"scope": "this_week"}).status_code == 404
    assert client.post("/api/proposals/nope/accept").status_code == 404


def test_versions_and_proposals_routes():
    client = TestClient(main.app)
    plan_file = storage.load_plan("plan")
    proposal = good_proposal(plan_file)
    adapt.accept(proposal.proposal_id, TODAY)

    assert [p["proposal_id"] for p in client.get("/api/plans/plan/proposals").json()] == [proposal.proposal_id]
    versions = client.get("/api/plans/plan/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["history"][0]["rationale"] == "Calf niggle, so ease off."
    assert client.get("/api/plans/plan/versions/1").json()["meta"]["version"] == 1
    assert client.get("/api/plans/plan/versions/7").status_code == 404


def swap_sessions(week, first, second):
    for session in week["sessions"]:
        if session["day"] == first:
            session["day"] = "pending"
        elif session["day"] == second:
            session["day"] = first
    for session in week["sessions"]:
        if session["day"] == "pending":
            session["day"] = second


def test_a_swap_goes_through_with_the_trade_off_shown(active):
    """Week 5: intervals move to the Saturday before the long run. Worth a warning, not a block."""
    full = change_result(active, list(range(2, 11)), lambda weeks: swap_sessions(weeks[3], "Thu", "Sat"))
    full["changes"] = [
        {"week_number": 5, "day": "Thu", "summary": "Easy run moved here"},
        {"week_number": 5, "day": "Sat", "summary": "Intervals moved here"},
    ]
    proposal, client = propose(active, full, scope="rest_of_plan")

    assert len(client.prompts) == 1 and proposal.acceptable
    assert proposal.warnings == ["Hard runs on consecutive days: Week 5 Sat and Week 5 Sun"]
    merged = adapt.accept(proposal.proposal_id, TODAY)
    week = merged.plan.weeks[4]
    assert next(s for s in week.sessions if s.day == "Sat").type == "intervals"
    assert next(s for s in week.sessions if s.day == "Thu").type == "easy"


def test_a_one_off_long_run_move_is_a_warning(active):
    def edit(weeks):
        swap_sessions(weeks[0], "Sat", "Sun")

    result = change_result(active, [2], edit)
    result["changes"] = [{"week_number": 2, "day": "Sat", "summary": "Long run a day earlier for a wedding"}]
    proposal, _ = propose(active, result)
    assert proposal.acceptable
    assert any("long run moves to Sat, away from your usual Sun" in w for w in proposal.warnings)


def test_a_dropped_session_needs_its_own_change_entry(active):
    def edit(weeks):
        weeks[0]["sessions"] = [s for s in weeks[0]["sessions"] if s["day"] != "Sat"]

    bad = change_result(active, [2], edit)
    bad["changes"][0]["day"] = "Sun"  # no entry on an easy day
    proposal, client = propose(active, bad, bad)
    assert not proposal.acceptable and "without its own change entry" in proposal.result.rationale
    assert "Fix these problems" in client.prompts[1]


def test_a_dropped_session_with_a_reason_is_allowed(active):
    def edit(weeks):
        weeks[0]["sessions"] = [s for s in weeks[0]["sessions"] if s["day"] != "Sat"]

    good = change_result(active, [2], edit)
    good["changes"] = [{"week_number": 2, "day": "Sat", "summary": "Rest instead: calf pain"}]
    assert propose(active, good)[0].acceptable


def test_a_downgraded_session_needs_its_own_change_entry(active):
    def edit(weeks):
        next(s for s in weeks[0]["sessions"] if s["day"] == "Sun").update(type="easy", steps=[])

    bad = change_result(active, [2], edit)
    assert "a long session (Sun)" in propose(active, bad, bad)[0].result.rationale


def test_warnings_do_not_block_accepting(active):
    result = change_result(active, [2], lambda weeks: swap_sessions(weeks[0], "Sat", "Sun"))
    result["changes"] = [{"week_number": 2, "day": "Sat", "summary": "x"}]
    proposal, _ = propose(active, result)
    assert adapt.accept(proposal.proposal_id, TODAY).meta.version == 2


def test_a_warning_already_on_the_plan_is_not_repeated(active):
    active.plan.weeks[4].planned_km = 200  # a volume jump the plan already has
    storage.save_plan(active)
    proposal, _ = propose(active, change_result(active, [2], soften_thursday))
    assert proposal.warnings == []
