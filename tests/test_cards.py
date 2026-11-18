"""Cards are built in code from the workspace, the same way every time."""
import pytest

from app import cards, mirror, plan, workspace
from tests.conftest import approve, week_draft


def test_a_week_shows_each_session_with_its_state(demo_ws):
    week = cards.week_card(3)
    states = {s["title"]: s["state"] for s in week["sessions"]}
    assert week["week_number"] == 3 and week["of"] == 10
    assert "done" in states.values()
    done = next(s for s in week["sessions"] if s["state"] == "done")
    assert done["activities"][0]["key"] in {row["key"] for row in mirror.rows()}
    this = cards.week_card("this")
    assert this["current"] and {s["state"] for s in this["sessions"]} <= {"missed", "today", "upcoming", "done"}
    assert any(s["state"] == "today" for s in this["sessions"])
    assert cards.week_card("next")["week_number"] == this["week_number"] + 1
    with pytest.raises(cards.CardError, match="weeks 1 to 10"):
        cards.week_card(42)


def test_a_skipped_session_is_not_missed_and_not_counted(demo_ws):
    draft = week_draft(4)
    first = draft["weeks"][0]["sessions"][0]  # yesterday's run, not done
    first["status"], first["edit_note"] = "skipped", "Felt off"
    draft["changes"] = [{"week": 4, "day": first["day"], "summary": "skipped"}]
    before = cards.week_card(4)
    approve(plan.draft(draft))
    after = cards.week_card(4)
    assert next(s for s in after["sessions"] if s["id"] == first["id"])["state"] == "skipped"
    assert after["sessions_planned"] == before["sessions_planned"] - 1


def test_the_glance_header(demo_ws):
    glance = cards.glance()
    assert glance["has_plan"] and glance["days_to_race"] == 46 and glance["today"] == "2026-11-25"
    assert glance["sessions_today"][0]["title"] == "Easy ride 30 min"
    assert glance["next_hard"]["date"] > "2026-11-25" and glance["form"]["value"] == round(glance["form"]["fitness"] - glance["form"]["fatigue"], 1)
    assert glance["pending_proposals"] == []


def test_the_glance_header_with_nothing_yet(ws):
    glance = cards.glance()
    assert glance["has_plan"] is False and glance["form"] is None and glance["sessions_today"] == []
    with pytest.raises(cards.CardError, match="no plan yet"):
        cards.build("week")


def test_an_activity_card_has_the_planned_session_and_the_review_and_draws_nothing(demo_ws):
    row = next(r for r in mirror.rows() if r["sport"] == "run")
    notes = workspace.root() / "notes" / row["key"]
    notes.mkdir(parents=True)
    (notes / "review.md").write_text("Tempo done to target.")
    card = cards.build("activity", {"key": row["key"]})
    assert card["planned"]["modality"] == "run" and card["review"] == "Tempo done to target." and "source" not in card
    assert "series" not in card and card["link"] is None  # the demo provider has no page of its own to link to
    with pytest.raises(cards.CardError):
        cards.build("activity", {"key": "missing"})


def test_a_proposal_card_shows_the_difference_before_and_after_approval(demo_ws):
    draft = week_draft(6)
    easy = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "easy")
    easy["day"] = "Mon"
    draft["changes"] = [{"week": 6, "day": "Sat", "summary": "moved"}]
    proposal = plan.draft(draft)
    card = cards.build("proposal", {"id": proposal.id})
    assert card["status"] == "pending" and card["diff"][0]["entries"][0]["kind"] == "moved"
    assert any("usual training days" in w for w in card["warnings"])
    approve(proposal)
    after = cards.build("proposal", {"id": proposal.id})
    assert after["status"] == "approved" and after["diff"][0]["entries"][0]["kind"] == "moved"  # still what it changed


def test_the_plan_card(demo_ws):
    overview = cards.build("plan")
    assert overview["days_to_go"] == 46 and overview["paces"]["threshold"] and overview["current_week"] == 4
    phases = overview["phases"]
    assert phases[0]["phase"] == "base" and phases[0]["from_week"] == 1 and phases[0]["start_date"] == overview["all_weeks"][0]["start_date"]
    assert [p["to_week"] + 1 for p in phases[:-1]] == [p["from_week"] for p in phases[1:]] and phases[-1]["to_week"] == 10
    assert sum(p["current"] for p in phases) == 1 and round(sum(p["planned_km"] for p in phases), 1) == round(sum(w["planned_km"] for w in overview["all_weeks"]), 1)
    weeks = overview["all_weeks"]
    assert [w["week_number"] for w in weeks] == list(range(1, 11)) and [w["week_number"] for w in weeks if w["current"]] == [4]
    assert all(w["sessions"] for w in weeks) and weeks[0]["sessions"][0]["state"] in ("done", "missed") and weeks[9]["sessions"][-1]["type"] == "race"
    assert not weeks[3]["future"] and weeks[4]["future"]


def test_the_provider_links_to_its_own_charts():
    from app.providers.intervals_icu import IntervalsIcu

    provider = IntervalsIcu("i1", "key")
    assert provider.web_url("activity", "i555") == "https://intervals.icu/activities/i555"
    assert provider.web_url("fitness") == "https://intervals.icu/fitness" and provider.web_url("activity") == "" and provider.web_url("other") == ""


def test_a_day_card_gathers_the_morning_status_the_check_ins_and_the_training(demo_ws, monkeypatch):
    from app.hub import hub

    monkeypatch.setattr(workspace, "now_iso", lambda: "2026-11-25T06:40:00+00:00")
    hub.stream.append("message", role="coach", text="HRV in range. Easy ride today.", origin="morning_brief")
    hub.stream.append("message", role="coach", text="Not a status.", origin="athlete")
    hub.stream.append("clear")  # a cleared conversation still belongs to its day
    (workspace.root() / "checkins").mkdir(exist_ok=True)
    (workspace.root() / "checkins" / "2026-11-25.md").write_text("- 07:10 morning: slept well")
    today = cards.build("day")
    assert today["is_today"] and today["date"] == "2026-11-25" and today["next"] is None and today["previous"] == "2026-11-24"
    assert [said["text"] for said in today["coach"]] == ["HRV in range. Easy ride today."] and today["checkins"] == "- 07:10 morning: slept well"
    assert today["sessions"][0]["title"] == "Easy ride 30 min" and today["wellness"]["form"] == cards.glance()["form"]["value"]
    done = next(row for row in mirror.rows() if row["sport"] == "run")
    earlier = cards.build("day", {"date": done["date"]})
    assert not earlier["is_today"] and earlier["next"] and earlier["coach"] == [] and earlier["checkins"] == ""
    assert done["key"] in {a["key"] for a in earlier["activities"]}
    assert cards.build("day", {"date": "yesterday"})["date"] == "2026-11-24"
    with pytest.raises(cards.CardError, match="YYYY-MM-DD"):
        cards.build("day", {"date": "soon"})


def test_static_cards_are_checked_so_the_agent_gets_a_usable_error():
    table = cards.build("table", data={"title": "Easy runs", "columns": ["Date", "Pace"], "rows": [["2026-11-03", "5:40"]]})
    assert table["columns"] == ["Date", "Pace"]
    with pytest.raises(cards.CardError, match="every row as long as columns"):
        cards.build("table", data={"columns": ["a", "b"], "rows": [[1]]})
    with pytest.raises(cards.CardError, match="no 'chart' card"):  # the app draws no charts of its own
        cards.build("chart", data={"kind": "line", "x": [1], "series": [{"values": [1]}]})
    with pytest.raises(cards.CardError, match="no 'fitness' card"):  # the day card has those numbers
        cards.build("fitness")
    with pytest.raises(cards.CardError, match="two to six"):
        cards.build("choice", data={"question": "Which day?", "options": ["Tue"]})
    with pytest.raises(cards.CardError, match="no 'pie' card"):
        cards.build("pie")
