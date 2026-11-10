"""The agents' door to the server: a token decides who is asking, and no route applies a plan."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import agent_api, analyst, plan, workspace
from app.agent import Agent
from app.hub import hub
from app.runtime.scripted import Scripted
from tests.conftest import week_draft


@pytest.fixture
def api(demo_ws):
    with TestClient(agent_api.create()) as client:
        yield client


def as_role(role: str) -> dict:
    return {"X-OpenStride-Token": hub.token_for(role)}


def test_no_route_approves_or_applies_a_plan():
    paths = {route.path for route in agent_api.create().routes}
    assert not any("approve" in path or "apply" in path for path in paths)
    assert {"/show", "/ask", "/plan/draft", "/analyse", "/call/{name}", "/whoami"} <= paths


def test_a_call_needs_a_live_token(api):
    assert api.get("/whoami").status_code == 401
    assert api.get("/whoami", headers={"X-OpenStride-Token": "made-up"}).status_code == 401
    assert api.get("/whoami", headers=as_role("coach")).json() == {"role": "coach"}
    assert api.post("/show", json={"card": "week"}).status_code == 401


def test_show_validates_and_puts_a_card_in_the_stream(api):
    assert api.post("/show", json={"card": "week", "args": {"week": "next"}}, headers=as_role("coach")).status_code == 200
    refused = api.post("/show", json={"card": "week", "args": {"week": 99}}, headers=as_role("coach"))
    assert refused.status_code == 422 and "weeks 1 to 10" in refused.json()["error"]
    table = {"columns": ["Week", "km"], "rows": [[1, 42.0]]}
    assert api.post("/show", json={"card": "table", "data": table}, headers=as_role("analyst")).status_code == 200
    assert api.post("/ask", json={"question": "Which day?", "options": ["Tue", "Wed"]}, headers=as_role("coach")).status_code == 200
    events = hub.stream.history()
    assert [(e["card"], e["by"]) for e in events] == [("week", "coach"), ("table", "analyst"), ("choice", "coach")]
    assert events[0]["data"] is None and events[1]["data"]["rows"] == [[1, 42.0]]  # a week is rebuilt when shown; a table is what was computed


def test_a_draft_through_the_api_is_a_proposal_and_nothing_more(api):
    before = (workspace.root() / "plan" / "plan.json").read_text()
    draft = week_draft(6, "Shorter Saturday.")
    easy = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "easy")
    easy["distance_km"] -= 1
    draft["changes"] = [{"week": 6, "day": easy["day"], "summary": "shorter"}]
    made = api.post("/plan/draft", json={"payload": draft}, headers=as_role("analyst")).json()
    assert made["kind"] == "change" and made["weeks_changed"] == [6]
    assert plan.load_proposal(made["proposal"]).author == "analyst"
    assert (workspace.root() / "plan" / "plan.json").read_text() == before
    assert hub.stream.history()[-1]["type"] == "proposal"
    bad = week_draft(1)
    bad["weeks"][0]["sessions"][0]["distance_km"] = 50
    refused = api.post("/plan/draft", json={"payload": bad}, headers=as_role("coach"))
    assert refused.status_code == 422 and any("already happened" in p for p in refused.json()["problems"])


def test_the_analyst_reads_and_reports_but_the_coach_acts(api):
    analyst_token = as_role("analyst")
    assert api.post("/call/source_capabilities", json={}, headers=analyst_token).json()["provider"] == "demo"
    key = json.loads((workspace.root() / "activities" / "index.jsonl").read_text().splitlines()[0])["key"]
    assert api.post("/call/source_streams", json={"args": {"reference": key}}, headers=analyst_token).json()["path"] == f"activities/{key}.streams.json"
    for name in ("source_sync", "source_log", "source_wellness", "source_push", "restore", "commit"):
        refused = api.post(f"/call/{name}", json={}, headers=analyst_token)
        assert refused.status_code == 422 and "coach's to do" in refused.json()["error"]
    assert api.post("/analyse", json={"question": "x"}, headers=analyst_token).status_code == 422
    assert api.post("/call/plan_apply", json={}, headers=as_role("coach")).status_code == 404
    assert api.post("/call/save_upload", json={}, headers=as_role("coach")).status_code == 404


def test_the_coach_logs_a_session_and_a_wellness_note_at_the_provider(api):
    coach = as_role("coach")
    logged = api.post("/call/source_log", json={"args": {"entry": {"sport": "strength", "minutes": 30, "rpe": 6, "title": "Gym"}}}, headers=coach).json()
    assert logged["logged"] and len(logged["new"]) == 1 and "strength" in logged["new"][0]
    future = api.post("/call/source_log", json={"args": {"entry": {"sport": "run", "minutes": 30, "date": "2026-12-25"}}}, headers=coach)
    assert future.status_code == 422 and "not in the future" in future.json()["error"]
    noted = api.post("/call/source_wellness", json={"args": {"day": "today", "fields": {"soreness": 3}}}, headers=coach).json()
    assert noted["date"] == "2026-11-25"
    line = next(l for l in (workspace.root() / "wellness" / "2026-11.jsonl").read_text().splitlines() if '"2026-11-25"' in l)
    assert json.loads(line)["soreness"] == 3
    assert workspace.changed(["activities", "wellness"]) == []


def test_restore_puts_back_only_what_the_agent_authors(api):
    notes = workspace.root() / "athlete" / "memory.md"
    good = notes.read_text()
    old = workspace.head()
    notes.write_text("ruined")
    workspace.commit("oops")
    coach = as_role("coach")
    assert api.post("/call/restore", json={"args": {"path": str(notes), "revision": old}}, headers=coach).json()["restored"] == "athlete/memory.md"
    assert notes.read_text() == good
    refused = api.post("/call/restore", json={"args": {"path": "plan/plan.json", "revision": old}}, headers=coach)
    assert refused.status_code == 422 and "through a proposal" in refused.json()["error"]


def test_the_analyst_runs_in_its_own_folder_and_its_report_is_filed(demo_ws):
    def script(request):
        scratch = request.policy.scratch
        assert request.policy.role == "analyst" and request.policy.cwd == scratch and "analyst" in request.system_prompt
        assert (scratch / ".claude" / "skills" / "analysis" / "SKILL.md").exists()
        yield ("tool", "Write", {"file_path": "report/report.md"}, lambda: (scratch / "report" / "report.md").write_text("# Easy pace is steady\n\n5:41/km at 140 bpm.\n"))
        yield ("tool", "Write", {"file_path": "report/scan.py"}, lambda: (scratch / "report" / "scan.py").write_text("print(1)\n"))
        yield ("tool", "Write", {"file_path": "report/big.bin"}, lambda: (scratch / "report" / "big.bin").write_bytes(b"x"))
        yield ("tool", "Write", {"file_path": str(workspace.root() / "journal" / "sneak.md")}, lambda: (workspace.root() / "journal" / "sneak.md").write_text("no"))
        yield ("say", "Easy pace at 140 bpm is steady at 5:41/km over four weeks.")

    async def go():
        hub.bind(asyncio.get_running_loop())
        hub.runtime = Scripted(script)
        hub.agent = Agent(hub, hub.runtime)
        started = analyst.start(hub, "Is my easy pace drifting?", context="Asked on a Wednesday.")
        return await analyst.wait(started["id"], 10)

    result = asyncio.run(go())
    assert result["state"] == "done" and result["summary"].startswith("Easy pace at 140 bpm")
    folder = workspace.root() / "reports" / result["report"]
    assert (folder / "report.md").exists() and (folder / "scan.py").exists() and not (folder / "big.bin").exists()
    assert not (workspace.root() / "journal" / "sneak.md").exists()
    assert analyst.reports()[0]["title"] == "Easy pace is steady"
    assert workspace.changed(["reports"]) == [] and workspace.log(limit=1)[0]["message"] == "Report: Easy pace is steady"
    assert "Asked on a Wednesday." in hub.runtime.requests[0].prompt
    assert hub.agent.pending == []  # someone was waiting for it, so no brief is queued


def test_a_run_nobody_waited_for_briefs_the_coach(demo_ws):
    async def go():
        hub.bind(asyncio.get_running_loop())
        hub.runtime = Scripted(lambda request: [("say", "Nothing unusual.")])
        hub.agent = Agent(hub, hub.runtime)
        started = analyst.start(hub, "Anything odd in wellness?")
        await analyst._tasks[started["id"]]
        return hub.agent.pending

    pending = asyncio.run(go())
    assert len(pending) == 1 and pending[0].origin == "analyst_done" and "Nothing unusual." in pending[0].text
