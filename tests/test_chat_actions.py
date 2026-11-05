import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import adapt, chat, lifecycle, main, memory, progress, storage
from app.models import PlanFile
from test_adapt_merge import change_result, soften_thursday
from test_generator import FakeClient

TODAY = date(2026, 11, 11)
NO_CHANGE = {"recommendation": "no_change", "rationale": "One run is not a trend.", "changes": [], "weeks": []}
USAGE = SimpleNamespace(input_tokens=100, output_tokens=200)


def reply_text(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)], usage=USAGE)


def reply_tool(name, call_id="call-1", text="", **args):
    content = [SimpleNamespace(type="text", text=text)] if text else []
    content.append(SimpleNamespace(type="tool_use", id=call_id, name=name, input=args))
    return SimpleNamespace(stop_reason="tool_use", content=content, usage=USAGE)


def propose(change_request="Make Thursday of this week easy", scope="this_week"):
    return reply_tool("propose_plan_change", scope=scope, change_request=change_request)


def structured(data):
    """A proposal reply, as the inner adaptation call returns it."""
    import json
    return reply_text(json.dumps(data))


@pytest.fixture(autouse=True)
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


def send(text, fake):
    return chat.send("plan", text, fake, TODAY)


def tool_results(fake, call_index=1):
    """The tool_result blocks the fake received in its nth call."""
    return fake.requests[call_index]["messages"][-1]["content"]


class LoopClient(FakeClient):
    """FakeClient that also keeps every request, since the loop sends more than one."""

    def __init__(self, *replies):
        super().__init__(*replies)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs | {"messages": list(kwargs["messages"])})  # the loop mutates its list
        return super().create(**kwargs)

    def stream(self, **kwargs):
        self.requests.append(kwargs | {"messages": list(kwargs["messages"])})
        return super().stream(**kwargs)


def test_an_answer_adds_one_coach_message_and_no_proposal(monkeypatch):
    calls = []
    monkeypatch.setattr(adapt, "create_proposal", lambda *a, **k: calls.append(a))
    result = send("Is my easy pace too fast?", LoopClient(reply_text("Your easy pace is fine.")))

    thread = storage.load_chat("plan")
    assert [m.role for m in thread] == ["user", "coach"]
    assert thread[1].text == "Your easy pace is fine."
    assert thread[1].action == "none" and thread[1].proposal_id == "" and not thread[1].memory_updated
    assert calls == [] and result["proposal"] is None
    assert storage.list_proposals("plan") == []


def test_propose_change_calls_the_adaptation_once_and_the_coach_explains_it(plan):
    fake = LoopClient(propose(), structured(change_result(plan, [2], soften_thursday)), reply_text("Thursday is now easy; accept it below."))
    result = send("My calf hurts, can we ease off?", fake)

    adaptation = fake.prompts[1]
    assert adaptation.startswith("Today:") and "Make Thursday of this week easy\n\nFrom chat:\nMy calf hurts" in adaptation
    [tool_result] = tool_results(fake, 2)
    assert tool_result["tool_use_id"] == "call-1" and tool_result["is_error"] is False
    assert '"recommendation": "change"' in tool_result["content"] and "Week 2 Thu" in tool_result["content"]
    coach = storage.load_chat("plan")[1]
    assert coach.text == "Thursday is now easy; accept it below." and coach.action == "proposal_created"
    assert coach.proposal_id == storage.list_proposals("plan")[0].proposal_id
    assert result["proposal"]["proposal_id"] == coach.proposal_id
    assert coach.usage.input_tokens == 200  # the two coach calls; the proposal's own call is billed on the proposal


def test_chat_never_changes_the_plan(plan):
    send("Ease off please", LoopClient(propose(), structured(change_result(plan, [2], soften_thursday)), reply_text("Done.")))
    assert storage.load_plan("plan") == plan
    proposal = storage.list_proposals("plan")[0]
    assert proposal.status == "pending" and proposal.origin == "chat"
    assert adapt.accept(proposal.proposal_id, TODAY).meta.version == 2


def test_no_change_from_the_adaptation_is_told_to_the_coach_and_saved(plan):
    fake = LoopClient(propose(), structured(NO_CHANGE), reply_text("I looked, and I don't recommend a change."))
    send("Change my plan", fake)
    [tool_result] = tool_results(fake, 2)
    assert "One run is not a trend." in tool_result["content"] and tool_result["is_error"] is False
    coach = storage.load_chat("plan")[1]
    assert coach.action == "none" and coach.proposal_id == ""
    saved = storage.list_proposals("plan")
    assert [p.result.recommendation for p in saved] == ["no_change"]
    assert progress.usage_totals("plan")["input_tokens"] == 300


def test_failed_adaptation_is_an_error_result_and_the_reply_stands(plan):
    fake = LoopClient(propose(), structured({"not": "valid"}), reply_text("I couldn't prepare that; let's try again tomorrow."))
    send("Change my plan", fake)
    [tool_result] = tool_results(fake, 2)
    assert tool_result["is_error"] is True and "could not be prepared" in tool_result["content"]
    coach = storage.load_chat("plan")[1]
    assert coach.action == "none" and coach.text.startswith("I couldn't prepare")


def test_a_second_proposal_in_one_message_is_refused():
    fake = LoopClient(
        propose(), structured(NO_CHANGE),
        reply_tool("propose_plan_change", call_id="call-2", scope="this_week", change_request="Try again"),
        reply_text("ok"),
    )
    send("Change my plan", fake)
    [second] = tool_results(fake, 3)
    assert second["is_error"] is True and "already prepared" in second["content"]
    assert len(storage.list_proposals("plan")) == 1


def test_empty_reply_with_a_proposal_gets_a_stand_in_text(plan):
    fake = LoopClient(propose(), structured(change_result(plan, [2], soften_thursday)), reply_text(""))
    send("Ease off", fake)
    assert storage.load_chat("plan")[1].text == "I've prepared the change below."


def test_a_loop_that_never_ends_is_an_error_and_leaves_the_thread_alone():
    notes = [reply_tool("update_notes", call_id=f"n{i}", content=f"# About the athlete\nversion {i}") for i in range(chat.MAX_CALLS)]
    with pytest.raises(chat.generator.GenerationError, match="did not finish"):
        send("hello", LoopClient(*notes))
    assert storage.load_chat("plan") == []


def test_failed_call_leaves_the_thread_alone():
    with pytest.raises(chat.generator.GenerationError):
        send("hello", LoopClient(reply_text("", stop_reason="max_tokens")))
    assert storage.load_chat("plan") == []


def test_update_notes_writes_the_memory_file_and_marks_the_message():
    fake = LoopClient(reply_tool("update_notes", content="# About the athlete\nPrefers morning runs.\n"), reply_text("Noted."))
    send("I prefer running in the morning", fake)
    assert memory.load() == "# About the athlete\nPrefers morning runs.\n"
    assert memory.status()["updated_by"] == "coach"
    coach = storage.load_chat("plan")[1]
    assert coach.memory_updated and coach.text == "Noted."
    [tool_result] = tool_results(fake, 1)
    assert tool_result["content"] == "Notes saved." and tool_result["is_error"] is False


def test_too_long_notes_are_refused_with_the_reason():
    fake = LoopClient(reply_tool("update_notes", content="x" * (memory.MAX_CHARS + 1)), reply_text("Too long, I'll condense."))
    send("hello", fake)
    [tool_result] = tool_results(fake, 1)
    assert tool_result["is_error"] is True and "Condense" in tool_result["content"]
    assert memory.load() == "" and not storage.load_chat("plan")[1].memory_updated


def test_unknown_tool_is_an_error_result():
    fake = LoopClient(reply_tool("delete_plan"), reply_text("Sorry."))
    send("hello", fake)
    [tool_result] = tool_results(fake, 1)
    assert tool_result["is_error"] is True


def test_the_coach_sees_its_notes_the_tools_and_the_cache_setting():
    memory.save("# About the athlete\nHas a history of calf trouble.", by="athlete")
    fake = LoopClient(reply_text("ok"))
    send("hello", fake)
    request = fake.requests[0]
    assert "Has a history of calf trouble." in request["system"]
    assert [tool["name"] for tool in request["tools"]] == ["propose_plan_change", "workout_details", "update_notes"]
    assert request["cache_control"] == {"type": "ephemeral"} and request["output_config"] == {"effort": "high"}
    assert request["model"] == "claude-opus-5-5"


def test_empty_notes_show_the_template_to_the_coach():
    fake = LoopClient(reply_text("ok"))
    send("hello", fake)
    assert "Nothing written yet" in fake.requests[0]["system"] and "# Open questions" in fake.requests[0]["system"]


def test_context_has_the_snapshot_history_and_at_most_30_messages():
    old = [chat.message("user" if i % 2 == 0 else "coach", f"message-{i:02d}") for i in range(40)]
    storage.save_chat("plan", old)
    fake = LoopClient(reply_text("ok"))
    send("latest question", fake)
    prompt = fake.prompts[0]

    assert '"ctl_7d_change"' in prompt and '"weekly_km_4wk_avg"' in prompt
    assert prompt.count("message-") == 29  # the 29 newest plus the new question make 30
    assert "message-10" not in prompt and "message-39" in prompt
    assert "latest question" in prompt
    assert "Today: 2026-11-11" in prompt and "Race date: 2027-01-10" in prompt
    assert "How the plan got to this version" in prompt


def test_context_includes_logbook_and_proposals(plan, monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-11")
    storage.save_proposal(adapt.create_proposal("plan", "this_week", "x", FakeClient(structured(NO_CHANGE)), TODAY))
    client = TestClient(main.app)
    client.post("/api/plans/plan/manual", json={"date": "2026-11-03", "modality": "strength", "duration_min": 30, "note": "squats felt heavy"})
    fake = LoopClient(reply_text("ok"))
    send("hello", fake)
    assert "squats felt heavy" in fake.prompts[0] and "Pending and recent proposals" in fake.prompts[0]
    assert "One run is not a trend." in fake.prompts[0]


def test_usage_is_on_the_message_and_in_the_plan_total():
    send("hello", LoopClient(reply_text("ok")))
    coach = storage.load_chat("plan")[1]
    assert coach.usage.input_tokens == 100 and coach.model == "claude-opus-5-5"
    assert progress.usage_totals("plan")["input_tokens"] == 100


def test_clear_archives_the_thread_and_keeps_its_cost_and_the_notes():
    memory.save("# About the athlete\nkeep me", by="athlete")
    send("hello", LoopClient(reply_text("ok")))
    client = TestClient(main.app)
    assert client.post("/api/plans/plan/chat/clear").status_code == 204
    assert client.get("/api/plans/plan/chat").json() == []
    assert len(list(storage.plan_dir("plan").glob("chat_*.json"))) == 1
    fake = LoopClient(reply_text("ok"))
    send("again", fake)
    assert "hello" not in fake.prompts[0].split("Conversation so far")[1]
    assert "keep me" in fake.requests[0]["system"]
    assert progress.usage_totals("plan")["input_tokens"] == 200


def events(response):
    """(event, data) pairs from a server-sent-events body."""
    import json
    out = []
    for chunk in response.text.strip().split("\n\n"):
        event, data = chunk.split("\n", 1)
        out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_the_route_streams_the_words_then_the_saved_messages(plan, monkeypatch):
    fake = LoopClient(reply_tool("update_notes", text="Noting that.", content="# Profile\nx"), reply_text("Done."))
    monkeypatch.setattr(chat.generator, "make_client", lambda: fake)
    client = TestClient(main.app)
    response = client.post("/api/plans/plan/chat", json={"text": "I'm 34"})
    assert response.headers["content-type"].startswith("text/event-stream")
    got = events(response)
    assert got[:-1] == [
        ("status", "Reading your plan and runs…"),
        ("status", "Thinking…"), ("delta", "Noting that."), ("status", "Updating the notes about you…"),
        ("delta", "\n\n"), ("status", "Thinking…"), ("delta", "Done."),
    ]
    assert got[-1][0] == "done" and [m["role"] for m in got[-1][1]["messages"]] == ["user", "coach"]
    assert storage.load_chat("plan")[1].text == "Noting that.\n\nDone."


def test_a_failure_mid_stream_is_an_error_event(plan, monkeypatch):
    fake = LoopClient(reply_text("", stop_reason="max_tokens"))
    monkeypatch.setattr(chat.generator, "make_client", lambda: fake)
    assert events(TestClient(main.app).post("/api/plans/plan/chat", json={"text": "hi"}))[-1][0] == "error"
    assert storage.load_chat("plan") == []


def test_routes_and_non_active_plans(monkeypatch):
    fake = LoopClient(reply_text("ok"))
    monkeypatch.setattr(chat.generator, "make_client", lambda: fake)
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-11")
    client = TestClient(main.app)
    result = events(client.post("/api/plans/plan/chat", json={"text": "hello"}))[-1][1]
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


def test_old_messages_with_flags_still_load():
    data = chat.message("coach", "x").model_dump() | {"flags": ["watch the calf"]}
    del data["memory_updated"]
    loaded = type(chat.message("coach", "x")).model_validate(data)
    assert loaded.flags == ["watch the calf"] and loaded.memory_updated is False


def test_text_from_every_call_in_the_loop_is_kept(plan):
    first = propose()
    first.content.insert(0, SimpleNamespace(type="text", text="Here is what I'd change."))
    fake = LoopClient(first, structured(change_result(plan, [2], soften_thursday)), reply_text("Accept it below."))
    send("Ease off", fake)
    assert storage.load_chat("plan")[1].text == "Here is what I'd change.\n\nAccept it below."


def test_a_tool_call_written_as_text_gets_one_correction_turn():
    leaked = reply_text('Noted.\n\n```\n<invoke name="update_notes">\n<parameter name="content"># Profile</parameter>\n</invoke>\n```')
    fake = LoopClient(leaked, reply_tool("update_notes", content="# Profile\nage 34"), reply_text("Noted, properly this time."))
    send("I'm 34", fake)
    assert fake.requests[1]["messages"][-1] == {"role": "user", "content": chat.LEAK_NUDGE}
    assert memory.load() == "# Profile\nage 34\n"
    coach = storage.load_chat("plan")[1]
    assert coach.text == "Noted, properly this time." and coach.memory_updated


def test_a_second_leak_is_not_corrected_again():
    leaked = reply_text("<invoke>still text</invoke>")
    fake = LoopClient(leaked, leaked)
    send("hello", fake)
    assert len(fake.requests) == 2 and storage.load_chat("plan")[1].text == "<invoke>still text</invoke>"


def test_cached_tokens_are_counted_and_priced():
    cached = reply_text("ok")
    cached.usage = SimpleNamespace(input_tokens=4, output_tokens=200, cache_read_input_tokens=50000, cache_creation_input_tokens=1000)
    send("hello", LoopClient(cached))
    coach = storage.load_chat("plan")[1]
    assert (coach.usage.input_tokens, coach.usage.cache_read_input_tokens, coach.usage.cache_creation_input_tokens) == (4, 50000, 1000)
    totals = progress.usage_totals("plan")
    assert totals["input_tokens"] == 51004 and totals["cached_tokens"] == 50000
    expected = ((4 + 50000 * 0.1 + 1000 * 1.25) * 4 + 200 * 20) / 1_000_000
    assert totals["cost_usd"] == pytest.approx(expected, abs=0.0001)


def add_run(run_id="run1", plan_id="plan"):
    from app import activities
    path = Path("examples/activities/edge_cases/cycling_garmin-fenix-5.fit")
    parsed = activities.parse_bytes(path.name, path.read_bytes())
    activity = activities.summarise(parsed, path.name, plan_id, "", 0)
    activity.id = run_id
    storage.save_activity(activity)
    activities.store_series(activity, parsed)
    return activity


def test_workout_details_returns_the_whole_file_and_the_recording():
    add_run()
    fake = LoopClient(reply_tool("workout_details", activity_id="run1"), reply_text("Your cadence held."))
    send("How did my cadence hold up?", fake)
    result = tool_results(fake)[0]
    assert not result["is_error"]
    body = json.loads(result["content"])
    assert body["workout"]["extra"]["total_calories"] == 7 and body["workout"]["laps"][0]["extra"]["lap_trigger"] == "session_end"
    assert body["recording"]["columns"][0] == "km" and body["recording"]["rows"]
    assert "m/s" in body["units"]
    assert storage.load_chat("plan")[-1].text == "Your cadence held."


def test_the_context_lists_every_run_with_its_extras_but_without_lap_extras():
    add_run()
    fake = LoopClient(reply_text("ok"))
    send("hello", fake)
    prompt = fake.prompts[0]
    assert '"total_calories": 7' in prompt and '"lap_trigger"' not in prompt


def test_workout_details_refusals():
    add_run("other", plan_id="elsewhere")
    fake = LoopClient(
        reply_tool("workout_details", call_id="c1", activity_id="nope"),
        reply_tool("workout_details", call_id="c2", activity_id="other"),
        reply_text("ok"),
    )
    send("hello", fake)
    first, second = tool_results(fake, 1)[0], tool_results(fake, 2)[0]
    assert first["is_error"] and "no recorded workout" in first["content"]
    assert second["is_error"] and "another plan" in second["content"]


def test_at_most_four_runs_in_full_per_message():
    for i in range(5):
        add_run(f"run{i}")
    fake = LoopClient(*[reply_tool("workout_details", call_id=f"c{i}", activity_id=f"run{i}") for i in range(5)], reply_text("ok"))
    send("compare them all", fake)
    assert not tool_results(fake, 4)[0]["is_error"]
    fifth = tool_results(fake, 5)[0]
    assert fifth["is_error"] and "At most 4" in fifth["content"]


def test_the_first_byte_waits_for_nothing_but_the_checks(plan, monkeypatch):
    """A refused message is a plain error, not an error event; everything after that streams."""
    monkeypatch.setattr(chat.generator, "make_client", lambda: (_ for _ in ()).throw(chat.generator.GenerationError("This needs an API key, and none is set.")))
    response = TestClient(main.app).post("/api/plans/plan/chat", json={"text": "hi"})
    assert response.status_code == 502 and "API key" in response.json()["detail"]


def test_a_detail_call_is_announced_between_the_words():
    add_run()
    fake = LoopClient(reply_tool("workout_details", text="Let me look.", activity_id="run1"), reply_text("Steady."))
    got = [event for event in chat.stream("plan", "how was it?", fake, TODAY)]
    assert got == [
        ("status", "Reading your plan and runs…"), ("status", "Thinking…"), ("delta", "Let me look."),
        ("status", "Reading a run in full…"), ("delta", "\n\n"), ("status", "Thinking…"), ("delta", "Steady."),
    ]
