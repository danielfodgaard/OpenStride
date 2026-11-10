"""The turn loop, driven by a scripted agent through the real boundary and the real gate."""
import asyncio
import json

import pytest

from app import ops, plan, workspace
from app.agent import Agent
from app.hub import hub
from app.runtime.scripted import Scripted
from tests.conftest import run, week_draft


def start(script):
    hub.bind(asyncio.get_running_loop())
    hub.runtime = Scripted(script)
    hub.agent = Agent(hub, hub.runtime)
    hub.agent.start()
    return hub.agent


async def turn(agent, text, kind="message", origin="athlete"):
    agent.enqueue(kind, text, origin)
    await asyncio.wait_for(agent.idle.wait(), 10)


def write(path, content):
    target = workspace.root() / path
    return ("tool", "Write", {"file_path": path}, lambda: (target.parent.mkdir(parents=True, exist_ok=True), target.write_text(content)))


def kinds():
    return [(e["type"], e.get("role") or e.get("state") or e.get("card")) for e in hub.stream.history()]


def test_a_message_becomes_a_turn_a_reply_and_a_commit(demo_ws):
    def script(request):
        yield write("journal/2026-11-25.md", "Asked about the week.")
        yield ("tool", "Bash", {"command": "openstride show week"}, lambda: ops.show("coach", "week", {"week": "this"}))
        yield ("say", "Week four is a lighter one.")

    async def go():
        agent = start(script)
        await turn(agent, "How is the week?")
        await agent.stop()

    run(go())
    assert kinds() == [("turn", "started"), ("card", "week"), ("message", "coach"), ("turn", "ended")]
    assert (workspace.root() / "journal" / "2026-11-25.md").read_text() == "Asked about the week."
    assert workspace.log(limit=1)[0]["message"] == "athlete: How is the week?"
    assert workspace.changed(["journal"]) == []
    request = hub.runtime.requests[0]
    assert request.prompt.startswith("Today is Wednesday 2026-11-25.") and request.prompt.endswith("How is the week?")
    assert "You are the athlete's running coach" in request.system_prompt and "The demo provider" in request.system_prompt
    assert hub.tokens == {}  # the turn's token dies with the turn


def test_a_write_to_the_plan_is_refused_before_it_happens(demo_ws):
    before = (workspace.root() / "plan" / "plan.json").read_text()

    def script(request):
        yield write("plan/plan.json", "{}")
        yield ("say", "Done.")

    async def go():
        agent = start(script)
        await turn(agent, "Move my run")
        await agent.stop()

    run(go())
    assert (workspace.root() / "plan" / "plan.json").read_text() == before


def test_a_write_that_slips_past_the_check_is_undone_and_the_agent_is_told(demo_ws):
    before = (workspace.root() / "plan" / "plan.json").read_text()

    def tamper():  # what a script inside the shell could do, where the pre-check can't see
        (workspace.root() / "plan" / "plan.json").write_text('{"weeks": []}')
        (workspace.root() / "activities" / "index.jsonl").write_text("")
        (workspace.root() / "journal" / "kept.md").write_text("mine to write")

    def script(request):
        if "Second" in request.prompt:
            yield ("say", "Understood.")
            return
        yield ("tool", "Bash", {"command": "python3 scratch/rewrite.py"}, tamper)
        yield ("say", "I changed the plan.")

    async def go():
        agent = start(script)
        await turn(agent, "First")
        await turn(agent, "Second")
        await agent.stop()

    run(go())
    assert (workspace.root() / "plan" / "plan.json").read_text() == before
    assert (workspace.root() / "activities" / "index.jsonl").read_text() != ""
    assert (workspace.root() / "journal" / "kept.md").read_text() == "mine to write"
    second = hub.runtime.requests[1].prompt
    assert "[since your last turn]" in second and "the change was undone" in second and "plan/plan.json" in second
    assert plan.load() is not None


def test_a_draft_reaches_the_athlete_and_changes_nothing(demo_ws):
    before = (workspace.root() / "plan" / "plan.json").read_text()

    def script(request):
        draft = week_draft(6, "Easier Saturday, as you asked.")
        easy = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "easy")
        easy["distance_km"] -= 2
        draft["changes"] = [{"week": 6, "day": easy["day"], "summary": "shorter"}]
        yield ("tool", "Bash", {"command": "openstride plan draft --from scratch/d.json"}, lambda: ops.draft("coach", draft))
        yield ("say", "Drafted.")

    async def go():
        agent = start(script)
        await turn(agent, "Make Saturday easier")
        await agent.stop()

    run(go())
    assert ("proposal", None) in kinds()
    assert (workspace.root() / "plan" / "plan.json").read_text() == before
    assert len(plan.proposals("pending")) == 1


def test_a_brief_can_end_in_silence(demo_ws):
    async def go():
        agent = start(lambda request: [("say", "SILENT")])
        await turn(agent, "Morning. Follow the daily-brief skill.", kind="brief", origin="morning_brief")
        await agent.stop()

    run(go())
    assert kinds() == [("turn", "started"), ("turn", "ended")]
    assert "[brief: morning_brief]" in hub.runtime.requests[0].prompt
    assert hub.runtime.requests[0].effort == "medium"


def test_the_session_and_the_named_commit_carry_over(demo_ws):
    def script(request):
        yield write("athlete/memory.md", "# Profile\nPrefers mornings.\n")
        yield ("tool", "Bash", {"command": "openstride commit -m x"}, lambda: ops.commit("coach", "Notes: prefers mornings"))
        yield ("say", "Noted.")

    async def go():
        agent = start(script)
        await turn(agent, "I prefer mornings")
        await turn(agent, "Thanks")
        await agent.stop()

    run(go())
    assert hub.runtime.requests[0].session_id is None and hub.runtime.requests[1].session_id == "scripted"
    assert "Notes: prefers mornings" in [entry["message"] for entry in workspace.log(limit=3)]


def test_a_failing_runtime_is_reported_not_fatal(demo_ws):
    def script(request):
        raise RuntimeError("the harness fell over")
        yield

    async def go():
        agent = start(script)
        await turn(agent, "Hello")
        await turn(agent, "Still there?")
        await agent.stop()

    run(go())
    failed = [e for e in hub.stream.history() if e["type"] == "turn" and e["state"] == "failed"]
    assert len(failed) == 2 and "the harness fell over" in failed[0]["error"]


def test_the_queue_survives_a_restart(demo_ws):
    async def first():
        hub.bind(asyncio.get_running_loop())
        agent = Agent(hub, Scripted(lambda request: [("say", "ok")]))
        agent.enqueue("message", "Before the restart", "athlete")  # never started: the server dies here

    async def second():
        agent = start(lambda request: [("say", "Back.")])
        await asyncio.wait_for(agent.idle.wait(), 10)
        await agent.stop()

    run(first())
    assert json.loads((workspace.state() / "queue.json").read_text())[0]["text"] == "Before the restart"
    run(second())
    assert hub.runtime.requests[0].prompt.endswith("Before the restart")
    assert json.loads((workspace.state() / "queue.json").read_text()) == []
