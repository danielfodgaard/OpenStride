"""The openstride command as an agent types it."""
import json
import subprocess
import sys

import pytest

from app import cli, plan, workspace


def run_cli(*args, expect=0):
    """In-process, like the console script, with stdout and the exit code."""
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            sys.argv = ["openstride", *args]
            cli.main()
        except SystemExit as exit_:
            code = exit_.code if isinstance(exit_.code, int) else 1
            if not isinstance(exit_.code, int) and exit_.code:
                err.write(str(exit_.code))
    assert code == expect, err.getvalue() or out.getvalue()
    return out.getvalue(), err.getvalue()


def test_paces_and_dates_are_computed_in_code(ws):
    out, _ = run_cli("paces", "--race", "10k", "--time", "44:00", "--goal-race", "hm", "--goal-time", "1:30:00")
    paces = json.loads(out)
    assert paces["current_vdot"] == 46.5 and paces["threshold"] == "4:37/km" and paces["race_pace"] == "4:16/km" and paces["goal_vdot"] == 51.0
    out, _ = run_cli("dates", "--race", "2027-01-10")
    dates = json.loads(out)
    assert dates["race_weekday"] == "Sun" and dates["days_to_go"] == 46 and dates["weeks"] == 6 and dates["week_starts"][0]["monday"] == "2026-11-30"
    _, err = run_cli("paces", "--race", "10k", "--time", "44 minutes", expect=1)
    assert "h:mm:ss" in err


def test_status_and_activities_read_the_workspace(demo_ws):
    status = json.loads(run_cli("status")[0])
    assert status["week_number"] == 4 and status["today"] == "2026-11-25"
    assert json.loads(run_cli("status", "2")[0])["week_number"] == 2
    rows = json.loads(run_cli("activities", "--sport", "run", "--limit", "3")[0])
    assert len(rows) == 3 and all(r["sport"] == "run" and "session_id" in r for r in rows)


def test_export_edit_validate_is_the_way_to_a_draft(demo_ws):
    draft_path = workspace.root() / "scratch" / "draft.json"
    run_cli("plan", "export", "--weeks", "6", "-o", str(draft_path))
    draft = json.loads(draft_path.read_text())
    assert [w["week_number"] for w in draft["weeks"]] == [6] and all("date" not in s and s["id"] for s in draft["weeks"][0]["sessions"])
    easy = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "easy")
    easy["day"] = "Mon"
    draft["rationale"] = "Monday suits you better that week."
    draft["changes"] = [{"week": 6, "day": "Sat", "summary": "moved to Monday"}]
    draft_path.write_text(json.dumps(draft))
    result = json.loads(run_cli("plan", "validate", "--from", str(draft_path))[0])
    assert result["valid"] and any("usual training days" in w for w in result["warnings"])
    diff = json.loads(run_cli("plan", "diff", "--from", str(draft_path))[0])
    assert diff["diff"][0]["entries"][0]["kind"] == "moved"
    del draft["weeks"][0]["sessions"][0]
    draft["changes"] = []
    draft_path.write_text(json.dumps(draft))
    out, _ = run_cli("plan", "validate", "--from", str(draft_path), expect=1)
    assert any("without its own entry" in e for e in json.loads(out)["errors"])


def test_an_old_version_can_be_exported_to_draft_from(demo_ws):
    commit = json.loads(run_cli("plan", "history")[0])[0]["commit"]
    old = json.loads(run_cli("plan", "export", "--version", commit, "--weeks", "1")[0])
    assert old["weeks"][0]["week_number"] == 1
    _, err = run_cli("plan", "export", "--version", "deadbeef", expect=1)
    assert "no plan in commit" in err


def test_commands_that_reach_out_only_work_inside_a_turn(demo_ws, monkeypatch):
    monkeypatch.delenv("OPENSTRIDE_SPOOL", raising=False)
    monkeypatch.delenv("OPENSTRIDE_TURN", raising=False)
    for args in (["show", "week"], ["source", "sync"], ["analyse", "why"], ["commit", "-m", "x"], ["ask", "q", "--options", "a", "b"]):
        _, err = run_cli(*args, expect=1)
        assert "inside an agent's turn" in err
    draft = workspace.root() / "scratch" / "d.json"
    draft.parent.mkdir(exist_ok=True)
    draft.write_text(json.dumps(plan.as_draft(plan.load(), "x", [6])))
    _, err = run_cli("plan", "draft", "--from", str(draft), expect=1)
    assert "inside an agent's turn" in err


def test_plan_apply_from_the_command_line_still_needs_the_athletes_token(demo_ws):
    draft = plan.as_draft(plan.load(), "Shorter.", [6])
    easy = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "easy")
    easy["distance_km"] -= 1
    draft["changes"] = [{"week": 6, "day": easy["day"], "summary": "shorter"}]
    proposal = plan.draft(draft)
    _, err = run_cli("plan", "apply", proposal.id, "--approval", "guess", expect=1)
    assert "not a valid approval" in err
    run_cli("plan", "apply", proposal.id, "--approval", plan.approval_token(proposal))
    assert plan.load_proposal(proposal.id).status == "approved"


def test_settings_are_read_and_changed_with_their_types_checked(ws):
    assert run_cli("settings", "get", "triggers.morning_brief")[0].strip() == "False"
    run_cli("settings", "set", "triggers.morning_brief", "true")
    run_cli("settings", "set", "quiet_hours.from", "22:00")
    run_cli("settings", "set", "budget.monthly_usd", "15")
    settings = workspace.settings()
    assert settings["triggers"]["morning_brief"] is True and settings["quiet_hours"]["from"] == "22:00" and settings["budget"]["monthly_usd"] == 15
    _, err = run_cli("settings", "set", "triggers.morning_brief", "sometimes", expect=1)
    assert "takes a bool" in err
    _, err = run_cli("settings", "set", "triggers.nonsense", "true", expect=1)
    assert "no setting" in err


def test_inside_an_agents_shell_the_cli_finds_the_workspace_without_the_servers_env(demo_ws):
    """As the runtime starts it: no data-dir variable, no .env, only the workspace and a token."""
    env = {"PATH": "/usr/bin:/bin", "OPENSTRIDE_WORKSPACE": str(workspace.root()), "OPENSTRIDE_TURN": "t", "OPENSTRIDE_TODAY": "2026-11-25", "HOME": "/nonexistent"}
    done = subprocess.run([sys.executable, "-m", "app.cli", "status"], env=env, capture_output=True, text=True, cwd=workspace.root().parent)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["week_number"] == 4
    reach = subprocess.run([sys.executable, "-m", "app.cli", "show", "week"], env=env, capture_output=True, text=True, cwd=workspace.root().parent)
    assert reach.returncode == 1 and "inside an agent's turn" in reach.stderr


def test_the_cli_reaches_the_server_through_files_in_the_turns_spool_folder(demo_ws):
    """The whole path an agent's `openstride show week` takes: a real subprocess, the spool, the agent API, the stream."""
    import asyncio

    from app import agent_api, spool
    from app.agent import coach_spool
    from app.hub import hub

    async def go():
        hub.bind(asyncio.get_running_loop())
        folder = coach_spool()
        spool.register(folder)
        watcher = asyncio.create_task(spool.watch(agent_api.create()))
        env = {"PATH": "/usr/bin:/bin", "OPENSTRIDE_WORKSPACE": str(workspace.root()), "OPENSTRIDE_TURN": hub.token_for("coach"), "OPENSTRIDE_SPOOL": str(folder), "OPENSTRIDE_TODAY": "2026-11-25"}

        async def cli_(*args, token=None):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.cli", *args, env=env | ({"OPENSTRIDE_TURN": token} if token else {}), cwd=workspace.root(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(process.communicate(), 30)
            return process.returncode, out.decode(), err.decode()

        shown = await cli_("show", "week", "next")
        refused = await cli_("show", "week", "99")
        stranger = await cli_("show", "week", token="not-a-live-token")
        watcher.cancel()
        spool.unregister(folder)
        return shown, refused, stranger, sorted(p.name for p in folder.iterdir())

    shown, refused, stranger, left = asyncio.run(go())
    assert shown[0] == 0 and json.loads(shown[1])["shown"] == "week", shown
    assert refused[0] == 1 and "weeks 1 to 10" in refused[2]
    assert stranger[0] == 1 and "inside an agent's turn" in stranger[2]
    assert [e["card"] for e in hub.stream.history() if e["type"] == "card"] == ["week"]
    assert left == []  # every request and answer file is cleaned up


def test_every_server_backed_command_sends_a_body_that_can_be_written(demo_ws, monkeypatch):
    """Catches an argument that shadows something unserialisable: each command's request is captured and dumped."""
    sent = []

    def capture(method, path, body=None, timeout=120):
        json.dumps(body)
        sent.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(cli, "server", capture)
    draft = workspace.root() / "scratch" / "d.json"
    draft.parent.mkdir(exist_ok=True)
    draft.write_text(json.dumps(plan.as_draft(plan.load(), "x", [6])))
    table = workspace.root() / "scratch" / "t.json"
    table.write_text(json.dumps({"columns": ["a"], "rows": [[1]]}))
    for args in (
        ["analyse", "Is my easy pace drifting?", "--context", "asked today", "--web"], ["analyse", "--run", "2026-11-25T10-00_x", "--wait", "5"],
        ["show", "week", "next"], ["show", "activity", "abc"], ["show", "fitness", "--since", "2026-11-01"], ["show", "table", "--from", str(table)],
        ["ask", "Which day?", "--options", "Tue", "Wed"], ["plan", "draft", "--from", str(draft), "--rationale", "why"],
        ["source", "sync", "--since", "2026-11-01"], ["source", "streams", "abc"], ["source", "push", "--force"], ["source", "capabilities"],
        ["source", "wellness", "--set", "soreness=3", "--set", "comments=left calf"], ["source", "log", "--sport", "strength", "--minutes", "25", "--rpe", "5"],
        ["reports"], ["usage"], ["commit", "-m", "Notes updated"], ["restore", "athlete/memory.md", "--from", "abc123"],
    ):
        run_cli(*args)
    by_path = dict(sent)
    assert by_path["/analyse"]["run"] == "2026-11-25T10-00_x" and sent[0][1] == {"question": "Is my easy pace drifting?", "web": True, "effort": "", "context": "asked today", "run": "", "wait": 540}
    assert by_path["/call/source_wellness"] == {"args": {"day": "today", "fields": {"soreness": 3, "comments": "left calf"}}}
    assert by_path["/call/source_log"] == {"args": {"entry": {"sport": "strength", "date": "today", "minutes": 25.0, "rpe": 5}}}
    assert by_path["/plan/draft"]["payload"]["rationale"] == "why"
