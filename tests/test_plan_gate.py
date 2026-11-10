"""The one way the plan changes: a draft, the athlete's approval, then apply."""
import json

import pytest

from app import gate, plan, workspace
from tests.conftest import approve, session_in, set_today, week_draft


def moved(week=6, title_type="easy", to="Fri"):
    draft = week_draft(week)
    session = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == title_type)
    draft["changes"] = [{"week": week, "day": session["day"], "summary": f"moved to {to}"}]
    session["day"] = to
    return draft, session["id"]


def test_a_draft_does_not_change_the_plan(demo_ws):
    before = (workspace.root() / "plan" / "plan.json").read_text()
    draft, _ = moved()
    proposal = plan.draft(draft)
    assert proposal.status == "pending" and proposal.kind == "change"
    assert (workspace.root() / "plan" / "plan.json").read_text() == before
    assert (workspace.root() / "proposals" / f"{proposal.id}.json").exists()


def test_apply_needs_the_token_for_this_proposal(demo_ws):
    draft, _ = moved()
    proposal = plan.draft(draft)
    for bad in ("", "not-a-token", gate.token("another-proposal", proposal.base_commit), gate.token(proposal.id, "another-commit")):
        with pytest.raises(plan.PlanError, match="not a valid approval"):
            plan.apply(proposal.id, bad)
    assert plan.load_proposal(proposal.id).status == "pending"


def test_approval_writes_the_plan_and_commits_it(demo_ws):
    draft, session_id = moved()
    proposal = plan.draft(draft)
    doc = approve(proposal)
    session = doc.plan.session(session_id)
    assert session.day == "Fri" and session.date.strftime("%a") == "Fri"
    assert session.moved_from is not None and session.moved_from.strftime("%a") != "Fri"
    assert plan.load().plan.session(session_id).day == "Fri"
    assert plan.load_proposal(proposal.id).status == "approved"
    assert workspace.log("plan/plan.json", limit=1)[0]["message"].startswith("Plan (change, approved)")
    assert workspace.changed(["plan", "proposals"]) == []


def test_moving_a_session_back_clears_moved_from(demo_ws):
    draft, session_id = moved()
    original_day = plan.load().plan.session(session_id).day
    approve(plan.draft(draft))
    back = week_draft(6)
    item = next(s for s in back["weeks"][0]["sessions"] if s["id"] == session_id)
    back["changes"] = [{"week": 6, "day": item["day"], "summary": "back again"}]
    item["day"] = original_day
    doc = approve(plan.draft(back))
    assert doc.plan.session(session_id).moved_from is None


def test_a_proposal_goes_stale_when_the_plan_moved_under_it(demo_ws):
    first, _ = moved(week=6)
    second, _ = moved(week=7)
    a, b = plan.draft(first), plan.draft(second)
    approve(a)
    assert plan.load_proposal(b.id).status == "stale"
    with pytest.raises(plan.PlanError, match="already stale"):
        approve(plan.load_proposal(b.id))


def test_a_proposal_goes_stale_when_its_day_has_passed(demo_ws, monkeypatch):
    draft, session_id = moved(week=5, to="Fri")
    proposal = plan.draft(draft)
    set_today(monkeypatch, "2026-12-20")
    with pytest.raises(plan.PlanError, match="out of date"):
        approve(proposal)
    assert plan.load_proposal(proposal.id).status == "stale"
    assert plan.load().plan.session(session_id).day != "Fri"


def test_reject_keeps_the_plan_and_the_reason(demo_ws):
    draft, _ = moved()
    proposal = plan.draft(draft)
    rejected = plan.reject(proposal.id, "Friday is no good")
    assert rejected.status == "rejected" and rejected.decision_note == "Friday is no good"
    with pytest.raises(plan.PlanError, match="already rejected"):
        approve(rejected)


def test_a_draft_with_errors_is_refused_with_what_to_fix(demo_ws):
    draft = week_draft(1)
    draft["weeks"][0]["sessions"][0]["distance_km"] = 99
    with pytest.raises(plan.PlanError) as refused:
        plan.draft(draft)
    assert any("already happened" in problem for problem in refused.value.problems)
    assert plan.proposals() == []


def test_a_draft_needs_a_rationale_and_a_difference(demo_ws):
    draft, _ = moved()
    draft["rationale"] = " "
    with pytest.raises(plan.PlanError, match="rationale"):
        plan.draft(draft)
    with pytest.raises(plan.PlanError, match="same as the current plan"):
        plan.draft(week_draft(6))


def test_dates_ids_and_volume_are_computed_not_taken_from_the_agent(demo_ws):
    draft = week_draft(6)
    sessions = draft["weeks"][0]["sessions"]
    easy = next(s for s in sessions if s["type"] == "easy")
    easy["distance_km"] += 3
    sessions.append({"day": "Wed", "type": "easy", "title": "Extra easy 5 km", "distance_km": 5, "date": "1999-01-01", "target_pace": "5:30/km"})
    draft["weeks"][0]["planned_km"] = 1
    draft["changes"] = [{"week": 6, "day": "Wed", "summary": "an extra easy run"}]
    doc, errors, _, _ = plan.check(draft, new=False)
    assert errors == []
    week = doc.plan.week(6)
    added = next(s for s in week.sessions if s.title == "Extra easy 5 km")
    assert added.id and added.id not in {s.id for s in plan.load().plan.sessions()}
    assert added.date.strftime("%a") == "Wed" and added.modality == "run"
    assert week.planned_km == round(sum(s.distance_km for s in week.sessions if s.modality == "run"), 1)


def test_skipping_a_session_takes_it_out_of_the_weeks_volume(demo_ws):
    draft = week_draft(6)
    easy = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "easy")
    easy["status"], easy["edit_note"] = "skipped", "Travel"
    draft["changes"] = [{"week": 6, "day": easy["day"], "summary": "skipped: travel"}]
    before = plan.load().plan.week(6).planned_km
    doc = approve(plan.draft(draft))
    assert doc.plan.week(6).planned_km == round(before - easy["distance_km"], 1)


def test_a_new_plan_needs_a_valid_request_and_every_week(ws, monkeypatch):
    set_today(monkeypatch, "2026-10-31")
    legacy = json.loads((workspace.GUIDE_DIR.parent.parent / "examples" / "sample_plan_v3.json").read_text())
    payload = {"rationale": "A ten-week build.", "request": legacy["request"], "weeks": legacy["plan"]["weeks"]}
    with pytest.raises(plan.PlanError) as refused:
        plan.draft(payload | {"request": legacy["request"] | {"long_run_day": "Mon"}}, new=True)
    assert any("long_run_day" in problem for problem in refused.value.problems)
    with pytest.raises(plan.PlanError) as refused:
        plan.draft(payload | {"weeks": legacy["plan"]["weeks"][:-1]}, new=True)
    assert any("Expected 10 weeks" in problem or "race" in problem for problem in refused.value.problems)
    proposal = plan.draft(payload, new=True)
    assert proposal.kind == "new_plan" and proposal.base_commit == ""
    assert plan.load() is None
    doc = approve(proposal)
    assert len(doc.plan.weeks) == 10 and doc.paces.current_vdot > 40
    assert all(s.id for s in doc.plan.sessions())


def test_plan_history_is_git_history(demo_ws):
    draft, _ = moved()
    approve(plan.draft(draft))
    history = plan.history()
    assert len(history) == 2
    old = plan.at_version(history[-1]["commit"])
    assert old.plan.model_dump() != plan.load().plan.model_dump()


def test_the_diff_follows_sessions_by_id(demo_ws):
    draft, session_id = moved()
    draft["weeks"][0]["sessions"] = [s for s in draft["weeks"][0]["sessions"] if s["type"] != "strength"]
    doc, _, _, _ = plan.check(draft, new=False)
    entries = plan.diff(plan.load(), doc)[0]["entries"]
    kinds = {(e["kind"], (e.get("after") or e["before"])["id"]) for e in entries}
    assert ("moved", session_id) in kinds
    assert any(kind == "removed" for kind, _ in kinds)
