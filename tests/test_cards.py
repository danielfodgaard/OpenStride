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


def test_an_activity_card_has_the_planned_session_the_review_and_a_chart(demo_ws):
    row = next(r for r in mirror.rows() if r["sport"] == "run")
    notes = workspace.root() / "notes" / row["key"]
    notes.mkdir(parents=True)
    (notes / "review.md").write_text("Tempo done to target.")
    card = cards.build("activity", {"key": row["key"]})
    assert card["planned"]["modality"] == "run" and card["review"] == "Tempo done to target." and "source" not in card
    assert 100 < len(card["series"]["points"]) <= 600 and len(card["series"]["points"][0]) == 8
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


def test_fitness_volume_and_plan_cards(demo_ws):
    fitness = cards.build("fitness")
    assert fitness["latest"]["date"] == "2026-11-25" and fitness["race_date"] == "2027-01-10"
    volume = cards.build("volume")["weeks"]
    assert len(volume) == 10 and volume[0]["done_km"] > 0 and volume[-1]["future"]
    overview = cards.build("plan")
    assert overview["days_to_go"] == 46 and overview["phases"][0]["phase"] == "base" and overview["paces"]["threshold"]
    weeks = overview["all_weeks"]
    assert [w["week_number"] for w in weeks] == list(range(1, 11)) and [w["week_number"] for w in weeks if w["current"]] == [4]
    assert all(w["sessions"] for w in weeks) and weeks[0]["sessions"][0]["state"] in ("done", "missed") and weeks[9]["sessions"][-1]["type"] == "race"


def test_static_cards_are_checked_so_the_agent_gets_a_usable_error():
    table = cards.build("table", data={"title": "Easy runs", "columns": ["Date", "Pace"], "rows": [["2026-11-03", "5:40"]]})
    assert table["columns"] == ["Date", "Pace"]
    with pytest.raises(cards.CardError, match="every row as long as columns"):
        cards.build("table", data={"columns": ["a", "b"], "rows": [[1]]})
    chart = cards.build("chart", data={"title": "Pace at 140 bpm", "kind": "line", "x": ["w1", "w2"], "series": [{"name": "pace", "values": [340, None]}]})
    assert chart["series"][0]["values"] == [340, None]
    for bad in ({"kind": "pie", "x": [1], "series": [{"values": [1]}]}, {"kind": "line", "x": [1, 2], "series": [{"values": [1]}]}, {"kind": "bar", "x": [1], "series": [{"values": ["a"]}]}):
        with pytest.raises(cards.CardError):
            cards.build("chart", data=bad)
    with pytest.raises(cards.CardError, match="two to six"):
        cards.build("choice", data={"question": "Which day?", "options": ["Tue"]})
    with pytest.raises(cards.CardError, match="no 'pie' card"):
        cards.build("pie")
