from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import adapt, chat, lifecycle, main, progress, storage
from app.models import PlanFile
from test_adapt_merge import change_result, soften_thursday
from test_generator import FakeClient, reply as fake_message

TODAY = date(2026, 11, 11)
ANSWER = {"reply": "Your easy pace is fine.", "action": "none", "change_request": "", "scope": "this_week", "flags": []}
PROPOSE = {
    "reply": "Let's ease off Thursday.",
    "action": "propose_change",
    "change_request": "Make Thursday of this week easy",
    "scope": "this_week",
    "flags": ["Watch the calf"],
}
NO_CHANGE = {"recommendation": "no_change", "rationale": "One run is not a trend.", "changes": [], "weeks": []}


@pytest.fixture(autouse=True)
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


def replies(*items):
    return FakeClient(*[fake_message(item) for item in items])


def send(text, fake):
    return chat.send("plan", text, fake, TODAY)


def test_an_answer_adds_one_coach_message_and_no_proposal(monkeypatch):
    calls = []
    monkeypatch.setattr(adapt, "create_proposal", lambda *a, **k: calls.append(a))
    result = send("Is my easy pace too fast?", replies(ANSWER))

    thread = storage.load_chat("plan")
    assert [m.role for m in thread] == ["user", "coach"]
    assert thread[1].action == "none" and thread[1].proposal_id == ""
    assert calls == [] and result["proposal"] is None
    assert storage.list_proposals("plan") == []


def test_propose_change_calls_the_adaptation_once(monkeypatch, plan):
    calls = []
    real = adapt.create_proposal

    def spy(plan_id, scope, context, client, today, origin):
        calls.append((plan_id, scope, context))
        return real(plan_id, scope, context, FakeClient(fake_message(change_result(plan, [2], soften_thursday))), today, origin)

    monkeypatch.setattr(adapt, "create_proposal", spy)
    result = send("My calf hurts, can we ease off?", replies(PROPOSE))

    assert len(calls) == 1
    assert calls[0][:2] == ("plan", "this_week")
    assert calls[0][2].startswith("Make Thursday of this week easy\n\nFrom chat:\n")
    assert "My calf hurts" in calls[0][2]
    coach = storage.load_chat("plan")[1]
    assert coach.action == "proposal_created"
    assert coach.proposal_id == storage.list_proposals("plan")[0].proposal_id
    assert coach.flags == ["Watch the calf"]
    assert result["proposal"]["proposal_id"] == coach.proposal_id


def test_chat_never_changes_the_plan(plan):
    fake = replies(PROPOSE, change_result(plan, [2], soften_thursday))
    send("Ease off please", fake)
    assert storage.load_plan("plan") == plan
    proposal = storage.list_proposals("plan")[0]
    assert proposal.status == "pending"
    assert adapt.accept(proposal.proposal_id, TODAY).meta.version == 2


def test_no_change_from_the_adaptation_is_said_in_chat(plan):
    send("Change my plan", replies(PROPOSE, NO_CHANGE))
    coach = storage.load_chat("plan")[1]
    assert coach.action == "none" and coach.proposal_id == ""
    assert "One run is not a trend." in coach.text
    assert coach.text.startswith("Let's ease off Thursday.")


def test_chat_proposals_are_marked_and_a_no_change_is_still_saved_and_billed(plan):
    send("Change my plan", replies(PROPOSE, NO_CHANGE))
    saved = storage.list_proposals("plan")
    assert [p.origin for p in saved] == ["chat"] and saved[0].result.recommendation == "no_change"
    assert progress.usage_totals("plan")["input_tokens"] == 200


def test_form_proposals_are_adapt_and_old_files_default_to_it(plan):
    proposal = adapt.create_proposal("plan", "this_week", "", replies(NO_CHANGE), TODAY)
    assert proposal.origin == "adapt"
    data = proposal.model_dump(mode="json")
    del data["origin"], data["warnings"]
    old = type(proposal).model_validate(data)
    assert old.origin == "adapt" and old.warnings == []


def test_failed_adaptation_keeps_the_reply(plan):
    send("Change my plan", replies(PROPOSE, {"not": "valid"}))
    coach = storage.load_chat("plan")[1]
    assert coach.action == "none" and "couldn't prepare that change" in coach.text


def test_empty_change_request_retries_once():
    bad = PROPOSE | {"change_request": "  "}
    fake = replies(bad, ANSWER)
    send("hello", fake)
    assert len(fake.prompts) == 2 and "Fix these problems" in fake.prompts[1]
    assert storage.load_chat("plan")[1].text == ANSWER["reply"]


def test_still_empty_after_the_retry_becomes_a_plain_answer():
    bad = PROPOSE | {"change_request": ""}
    send("hello", replies(bad, bad))
    assert storage.load_chat("plan")[1].action == "none"
    assert storage.list_proposals("plan") == []


def test_failed_call_leaves_the_thread_alone():
    with pytest.raises(chat.generator.GenerationError):
        send("hello", replies(ANSWER | {"action": "bad"}))
    assert storage.load_chat("plan") == []


def test_context_has_the_snapshot_and_at_most_30_messages():
    old = [chat.message("user" if i % 2 == 0 else "coach", f"message-{i:02d}") for i in range(40)]
    storage.save_chat("plan", old)
    fake = replies(ANSWER)
    send("latest question", fake)
    prompt = fake.prompts[0]

    assert '"ctl_7d_change"' in prompt and '"weekly_km_4wk_avg"' in prompt
    assert prompt.count("message-") == 29  # the 29 newest plus the new question make 30
    assert "message-10" not in prompt and "message-39" in prompt
    assert "latest question" in prompt
    assert "Today: 2026-11-11" in prompt and "Race date: 2027-01-10" in prompt


def test_context_includes_logbook_and_proposals(plan, monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-11")
    storage.save_proposal(adapt.create_proposal("plan", "this_week", "x", replies(NO_CHANGE), TODAY))
    client = TestClient(main.app)
    client.post("/api/plans/plan/manual", json={"date": "2026-11-03", "modality": "strength", "duration_min": 30, "note": "squats felt heavy"})
    fake = replies(ANSWER)
    send("hello", fake)
    assert "squats felt heavy" in fake.prompts[0] and "Pending and recent proposals" in fake.prompts[0]
    assert "One run is not a trend." in fake.prompts[0]


def test_usage_is_on_the_message_and_in_the_plan_total():
    send("hello", replies(ANSWER))
    coach = storage.load_chat("plan")[1]
    assert coach.usage.input_tokens == 100 and coach.model == "claude-sonnet-5-5"
    assert progress.usage_totals("plan")["input_tokens"] == 100


def test_clear_archives_the_thread_and_keeps_its_cost():
    send("hello", replies(ANSWER))
    client = TestClient(main.app)
    assert client.post("/api/plans/plan/chat/clear").status_code == 204
    assert client.get("/api/plans/plan/chat").json() == []
    assert len(list(storage.plan_dir("plan").glob("chat_*.json"))) == 1
    fake = replies(ANSWER)
    send("again", fake)
    assert "hello" not in fake.prompts[0].split("Conversation so far")[1]
    assert progress.usage_totals("plan")["input_tokens"] == 200


def test_routes_and_non_active_plans(monkeypatch):
    fake = replies(ANSWER)
    monkeypatch.setattr(chat.generator, "make_client", lambda: fake)
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-11")
    client = TestClient(main.app)
    result = client.post("/api/plans/plan/chat", json={"text": "hello"}).json()
    assert [m["role"] for m in result["messages"]] == ["user", "coach"]
    assert client.post("/api/plans/plan/chat", json={"text": "   "}).status_code == 422

    other = storage.load_plan("plan")
    other.meta.id, other.meta.status = "next", "draft"
    storage.save_plan(other)
    assert client.post("/api/plans/next/chat", json={"text": "hi"}).status_code == 409
    lifecycle.accept("next")
    assert client.post("/api/plans/plan/chat", json={"text": "hi"}).status_code == 409
    assert client.post("/api/plans/plan/chat/clear").status_code == 409
    assert len(client.get("/api/plans/plan/chat").json()) == 2  # archived: still readable
