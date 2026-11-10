"""The `openstride` command: every action the app has, for the agents and for you.

Facts (paces, dates, validate, diff, status) are computed right here from the workspace files.
Anything that leaves the workspace (a card, a draft for the athlete, the provider, the analyst) is asked
of the server, over the socket and token an agent's turn is started with.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

RACES = {"5k": 5.0, "10k": 10.0, "hm": 21.0975, "half": 21.0975, "m": 42.195, "marathon": 42.195}


def out(value) -> None:
    print(value if isinstance(value, str) else json.dumps(value, indent=1, default=str))


def fail(message: str, problems=None, code: int = 1):
    print(message, file=sys.stderr)
    for problem in problems or []:
        print(f"- {problem}", file=sys.stderr)
    sys.exit(code)


def read_json(path: str):
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text()
        return json.loads(text)
    except OSError as error:
        fail(f"Can't read {path}: {error.strerror}.")
    except ValueError as error:
        fail(f"{path} is not valid JSON: {error}")


def server(method: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    """Ask the server, as the agent whose turn this shell belongs to."""
    import httpx

    socket, token = os.environ.get("OPENSTRIDE_AGENT_SOCKET"), os.environ.get("OPENSTRIDE_AGENT_TOKEN")
    if not socket or not token:
        fail("This command runs inside an agent's turn: it puts something in front of the athlete or reaches the provider. Outside one, use the app.")
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=socket), base_url="http://openstride", timeout=timeout) as client:
            response = client.request(method, path, json=body, headers={"X-OpenStride-Token": token})
    except httpx.HTTPError as error:
        fail(f"The OpenStride server did not answer ({type(error).__name__}). Try once more; if it persists, tell the athlete.")
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = {}
        fail(detail.get("error") or detail.get("detail") or f"The server answered {response.status_code}.", detail.get("problems"))
    return response.json()


def call(name: str, **args) -> dict:
    return server("POST", f"/call/{name}", {"args": {k: v for k, v in args.items() if v is not None}})


# --- facts -------------------------------------------------------------------------------------
def cmd_paces(a) -> None:
    from app.vdot import compute_paces

    distance = RACES.get(str(a.race).lower()) or float(a.race)
    goal_distance = (RACES.get(str(a.goal_race).lower()) or float(a.goal_race)) if a.goal_race else distance
    try:
        out(compute_paces(distance, a.time, goal_distance, a.goal_time))
    except ValueError as error:
        fail(str(error))


def cmd_dates(a) -> None:
    from app import clock
    from app.models import DAYS

    try:
        race, start = date.fromisoformat(a.race), date.fromisoformat(a.start) if a.start else clock.today()
    except ValueError:
        fail("Dates are YYYY-MM-DD.")
    starts = clock.week_starts(start, race)
    out({
        "today": clock.today().isoformat(), "race_date": race.isoformat(), "race_weekday": DAYS[race.weekday()], "days_to_go": (race - clock.today()).days,
        "weeks": len(starts), "week_starts": [{"week": i + 1, "monday": day.isoformat()} for i, day in enumerate(starts)],
    })


def cmd_status(a) -> None:
    from app import cards

    try:
        out(cards.status(a.week))
    except cards.CardError as error:
        fail(str(error))


def cmd_activities(a) -> None:
    from app import cards, mirror, plan

    rows = [r for r in mirror.rows() if (not a.since or r["date"] >= a.since) and (not a.sport or r["sport"] == a.sport)]
    found = cards.matches(plan.load())
    out([r | {"session_id": found.by_activity.get(r["key"], "")} for r in rows[-a.limit:]])


def cmd_settings(a) -> None:
    from app import workspace

    current = workspace.settings()
    if a.action == "get" or not a.action:
        value = current
        for part in (a.key or "").split("."):
            if part:
                value = value.get(part) if isinstance(value, dict) else None
        out(workspace.dump_yaml(value) if isinstance(value, (dict, list)) else str(value))
        return
    if not a.key or a.value is None:
        fail("Usage: openstride settings set <key.path> <value>")
    path = workspace.root() / "settings.yaml"
    if not path.parent.exists():
        fail("There is no workspace yet. Start the server once.")
    import yaml

    try:
        value = yaml.safe_load(a.value)
    except yaml.YAMLError:
        value = a.value
    node, parts = workspace.DEFAULT_SETTINGS, a.key.split(".")
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            fail(f"There is no setting {a.key}. `openstride settings get` shows them all.")
        node = node[part]
    if not isinstance(value, type(node)) and not (isinstance(node, float) and isinstance(value, int)):
        fail(f"{a.key} takes a {type(node).__name__}, like {node!r}.")
    stored = workspace.load_yaml(path, {})
    stored = stored if isinstance(stored, dict) else {}
    target = stored
    for part in parts[:-1]:
        target = target.setdefault(part, {}) if isinstance(target.get(part), dict) or part not in target else target[part]
    target[parts[-1]] = value
    workspace.write_file(path, workspace.dump_yaml(workspace.merged(workspace.DEFAULT_SETTINGS, stored)))
    out(f"{a.key} = {value!r}")


# --- the plan ----------------------------------------------------------------------------------
def load_draft(a) -> dict:
    payload = read_json(a.file)
    if not isinstance(payload, dict) or "weeks" not in payload:
        fail('A draft is a JSON object with "weeks" (the complete weeks you are changing), "rationale" and "changes". `openstride plan export` gives you one to start from.')
    if getattr(a, "rationale", None):
        payload["rationale"] = a.rationale
    return payload


def cmd_plan(a) -> None:
    from app import plan

    if a.action == "export":
        doc = plan.load() if not a.version else None
        if a.version:
            try:
                doc = plan.at_version(a.version)
            except Exception:
                fail(f"There is no plan in commit {a.version}. `openstride plan history` lists them.")
        if doc is None:
            fail("There is no plan yet. A new plan is drafted with --new; the new-plan skill has the shape.")
        weeks = [int(n) for n in a.weeks.split(",")] if a.weeks else None
        text = json.dumps(plan.as_draft(doc, "", weeks), indent=1)
        if a.output:
            Path(a.output).parent.mkdir(parents=True, exist_ok=True)
            Path(a.output).write_text(text)
            out(f"Wrote {a.output}. Edit the sessions, fill in rationale and changes, then `openstride plan draft --from {a.output}`.")
        else:
            out(text)
    elif a.action in ("validate", "diff"):
        try:
            doc, errors, warnings, _ = plan.check(load_draft(a), a.new)
        except plan.PlanError as error:
            fail(str(error), error.problems)
        if a.action == "validate":
            out({"valid": not errors, "errors": errors, "warnings": warnings})
            sys.exit(1 if errors else 0)
        out({"errors": errors, "warnings": warnings, "diff": plan.diff(None if a.new else plan.load(), doc)})
    elif a.action == "draft":
        out(server("POST", "/plan/draft", {"payload": load_draft(a), "new": a.new}))
    elif a.action == "history":
        out(plan.history())
    elif a.action == "apply":
        try:
            plan.apply(a.proposal, a.approval or "")
        except plan.PlanError as error:
            fail(str(error), error.problems)
        out("Applied.")


# --- into the stream ---------------------------------------------------------------------------
def cmd_show(a) -> None:
    args, data = {}, None
    if a.card == "week":
        args = {"week": a.target or "this"}
    elif a.card in ("session", "report", "proposal"):
        if not a.target:
            fail(f"Which {a.card}? Give its id.")
        args = {"id": a.target}
    elif a.card == "activity":
        if not a.target:
            fail("Which activity? Give its key from `openstride activities`.")
        args = {"key": a.target}
    elif a.card == "activities":
        args = {"limit": a.limit}
    elif a.card == "fitness":
        args = {k: v for k, v in (("from", a.since), ("to", a.until)) if v}
    elif a.card in ("table", "chart"):
        if not a.source:
            fail(f"`openstride show {a.card} --from <file.json>`: the data to show.")
        data = read_json(a.source)
    out(server("POST", "/show", {"card": a.card, "args": args, "data": data}))


def cmd_ask(a) -> None:
    out(server("POST", "/ask", {"question": a.question, "options": a.options}))


# --- the provider ------------------------------------------------------------------------------
def cmd_source(a) -> None:
    if a.action == "capabilities":
        out(call("source_capabilities"))
    elif a.action == "check":
        out(call("source_check"))
    elif a.action == "sync":
        out(call("source_sync", since=a.since))
    elif a.action == "streams":
        out(call("source_streams", reference=a.target))
    elif a.action == "push":
        out(call("source_push", force=a.force))
    elif a.action == "upload":
        out(call("source_upload", path=str(Path(a.target).resolve())))
    elif a.action == "wellness":
        fields = {}
        for item in a.set or []:
            key, _, value = item.partition("=")
            try:
                fields[key] = json.loads(value)
            except ValueError:
                fields[key] = value
        if not fields:
            fail("Give at least one field: --set soreness=3 --set comments='calf tight'")
        out(call("source_wellness", day=a.date or "today", fields=fields))
    elif a.action == "log":
        entry = {"sport": a.sport, "date": a.date or "today", "minutes": a.minutes, "distance_km": a.distance, "rpe": a.rpe, "title": a.title, "note": a.note, "time": a.time}
        out(call("source_log", entry={k: v for k, v in entry.items() if v is not None}))


def cmd_analyse(a) -> None:
    if not a.run and not a.question:
        fail('Usage: openstride analyse "<the question>" [--web], or --run <id> to keep waiting for one.')
    body = {"question": a.question or "", "web": a.web, "effort": a.effort or "", "context": a.context or "", "run": a.run or "", "wait": a.wait}
    out(server("POST", "/analyse", body, timeout=a.wait + 30))


# --- for you, outside an agent -----------------------------------------------------------------
def cmd_serve(a) -> None:
    import uvicorn

    from app import config

    problem = config.open_server_problem()
    if problem:  # the app's lifespan refuses too; here it is one clean line and no traceback
        sys.exit(problem)
    uvicorn.run("app.server:app", host=config.settings.host, port=config.settings.port)


def cmd_backup(a) -> None:
    from app import server as web

    out(str(web.write_backup(a.folder)))


def cmd_migrate(a) -> None:
    from app import config, migrate, workspace

    workspace.init()
    report = migrate.run(Path(a.source) if a.source else config.settings.data_dir, upload=a.upload, force=a.force)
    out("\n".join(report))


def cmd_doctor(a) -> None:
    from app import config, providers, workspace
    from app.providers import ProviderError
    from app.runtime import claude_sdk

    lines = [f"data folder: {config.settings.data_dir}", f"workspace: {'present' if (workspace.root() / '.git').exists() else 'not set up yet (start the server once)'}"]
    provider = providers.active()
    if provider is None:
        lines.append("provider: none. Set INTERVALS_ICU_API_KEY and INTERVALS_ICU_ATHLETE_ID in .env.")
    else:
        try:
            who = provider.check()
            lines.append(f"provider: {provider.name}, connected as {who.get('athlete') or who.get('id')}")
            if a.write and provider.capabilities.calendar:
                lines += write_test(provider)
        except ProviderError as error:
            lines.append(f"provider: {provider.name}, NOT working: {error}")
    lines.append(f"Anthropic API key: {'set' if os.environ.get('ANTHROPIC_API_KEY') else 'NOT set (ANTHROPIC_API_KEY in .env)'}")
    try:
        import claude_agent_sdk

        lines.append(f"agent harness: claude-agent-sdk {claude_agent_sdk.__version__}")
    except ImportError:
        lines.append("agent harness: NOT installed (uv sync)")
    if claude_sdk.sandbox_on():
        lines.append("sandbox: on. The agents' shells can write only what they author and reach no network.")
    else:
        lines.append("sandbox: OFF. The boundary is held by the tool-call check and the check after each turn. For the OS sandbox on Linux install bubblewrap and socat.")
    out("\n".join(lines))


def write_test(provider) -> list[str]:
    """Prove the calendar works, with a workout a year out that is removed again."""
    from datetime import timedelta

    from app import clock

    day = clock.today() + timedelta(days=400)
    session = {
        "id": "doctor-test", "date": day.isoformat(), "type": "intervals", "modality": "run", "title": "OpenStride test (delete me)",
        "distance_km": 5, "duration_min": 0, "target_pace": "4:30/km", "description": "A connection test.",
        "steps": [{"repeat": 1, "description": "Warm up", "distance_km": 2, "duration_min": 0, "target_pace": "5:30-6:00/km", "reps": 0, "load": ""},
                  {"repeat": 3, "description": "Rep", "distance_km": 0.4, "duration_min": 0, "target_pace": "4:00/km", "reps": 0, "load": ""},
                  {"repeat": 3, "description": "Jog recovery", "distance_km": 0, "duration_min": 2, "target_pace": "", "reps": 0, "load": ""}],
    }
    made = provider.put_event(session, {"cross_training": ""})
    provider.delete_event(made["id"])
    verdict = "read as a structured workout" if made["structured"] else "NOT read as a structured workout (it would show as text)"
    return [f"calendar: a test workout on {day} was created, {verdict}, and removed", *(f"  note: {note}" for note in made["notes"])]


def build() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openstride", description="OpenStride: your training data as a workspace. With no command, runs the server.")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("paces", help="VDOT and reference paces from a recent race")
    p.add_argument("--race", required=True, help="distance of the recent race: km, or 5k, 10k, hm, m")
    p.add_argument("--time", required=True, help="its time, h:mm:ss or mm:ss")
    p.add_argument("--goal-race", help="the goal race distance, if different")
    p.add_argument("--goal-time", help="the goal time, for goal VDOT and race pace")
    p.set_defaults(run=cmd_paces)

    p = sub.add_parser("dates", help="the plan's weeks for a race date: Mondays, weekday of the race, days to go")
    p.add_argument("--race", required=True, help="race date, YYYY-MM-DD")
    p.add_argument("--start", help="first day of the plan (default today)")
    p.set_defaults(run=cmd_dates)

    p = sub.add_parser("status", help="one week of the plan against what was done (default: this week)")
    p.add_argument("week", nargs="?", help="a week number, this, next or last")
    p.set_defaults(run=cmd_status)

    p = sub.add_parser("activities", help="the activity index, with the planned session each counts as")
    p.add_argument("--since", help="YYYY-MM-DD")
    p.add_argument("--sport", help="run, ride, swim, strength, …")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(run=cmd_activities)

    p = sub.add_parser("plan", help="export, validate, diff and draft plan changes; history")
    p.add_argument("action", choices=["export", "validate", "diff", "draft", "history", "apply"])
    p.add_argument("proposal", nargs="?", help="for apply: the proposal id")
    p.add_argument("--from", dest="file", default="-", help="the draft file (default: stdin)")
    p.add_argument("--new", action="store_true", help="the draft is a whole new plan with its request")
    p.add_argument("--rationale", help="why, for the athlete; overrides the draft's own")
    p.add_argument("--weeks", help="for export: week numbers, comma-separated (default: all)")
    p.add_argument("--version", help="for export: a commit from `plan history`, to start from an old version")
    p.add_argument("-o", "--output", help="for export: write to this file instead of stdout")
    p.add_argument("--approval", help="for apply: the athlete's approval token")
    p.set_defaults(run=cmd_plan)

    p = sub.add_parser("show", help="put a card in front of the athlete")
    p.add_argument("card", choices=["week", "session", "activity", "activities", "fitness", "volume", "plan", "report", "proposal", "table", "chart"])
    p.add_argument("target", nargs="?", help="week: number, this, next, last. session/report/proposal: its id. activity: its key")
    p.add_argument("--from", dest="source", help="table, chart: the JSON file to show")
    p.add_argument("--since", help="fitness: first day")
    p.add_argument("--until", help="fitness: last day")
    p.add_argument("--limit", type=int, default=10, help="activities: how many")
    p.set_defaults(run=cmd_show)

    p = sub.add_parser("ask", help="ask the athlete a question they answer with a tap")
    p.add_argument("question")
    p.add_argument("--options", nargs="+", required=True)
    p.set_defaults(run=cmd_ask)

    p = sub.add_parser("source", help="the provider: sync, recordings, calendar, wellness, hand-logged sessions")
    p.add_argument("action", choices=["capabilities", "check", "sync", "streams", "push", "upload", "wellness", "log"])
    p.add_argument("target", nargs="?", help="streams: an activity key or id. upload: a file under scratch/uploads/")
    p.add_argument("--since", help="sync: from this day, YYYY-MM-DD")
    p.add_argument("--force", action="store_true", help="push: send every session again, replacing what is on the calendar")
    p.add_argument("--date", help="wellness, log: YYYY-MM-DD (default today)")
    p.add_argument("--set", action="append", help="wellness: field=value, repeatable")
    p.add_argument("--sport", help="log: run, strength, ride, swim, walk, hike, row, ski, other")
    p.add_argument("--minutes", type=float, help="log: how long")
    p.add_argument("--distance", type=float, help="log: km")
    p.add_argument("--rpe", type=int, help="log: 1-10")
    p.add_argument("--title", help="log: what it was")
    p.add_argument("--note", help="log: anything the athlete said about it")
    p.add_argument("--time", help="log: start time, HH:MM")
    p.set_defaults(run=cmd_source)

    p = sub.add_parser("analyse", help="hand a question to the analyst, which computes over the data in a sandbox")
    p.add_argument("question", nargs="?")
    p.add_argument("--context", help="what from the conversation the analyst needs to know")
    p.add_argument("--web", action="store_true", help="let this run search and read the web")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--run", help="keep waiting for an earlier run")
    p.add_argument("--wait", type=float, default=540, help="seconds to wait before reporting it is still running")
    p.set_defaults(run=cmd_analyse)

    sub.add_parser("reports", help="what the analyst has produced").set_defaults(run=lambda a: out(call("reports")))
    sub.add_parser("usage", help="what the agents have cost this month").set_defaults(run=lambda a: out(call("usage")))

    p = sub.add_parser("commit", help="name this turn's commit: one line on what you changed and why")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(run=lambda a: out(call("commit", message=a.message)))

    p = sub.add_parser("restore", help="put a file you authored back as it was in an earlier commit")
    p.add_argument("path")
    p.add_argument("--from", dest="revision", required=True, help="a commit from git log")
    p.set_defaults(run=lambda a: out(call("restore", path=str(Path(a.path).resolve()), revision=a.revision)))

    p = sub.add_parser("settings", help="read or change settings.yaml")
    p.add_argument("action", nargs="?", choices=["get", "set"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(run=cmd_settings)

    sub.add_parser("serve", help="run the server (the default)").set_defaults(run=cmd_serve)
    p = sub.add_parser("backup", help="write a zip of the data folder")
    p.add_argument("folder", type=Path)
    p.set_defaults(run=cmd_backup)
    p = sub.add_parser("migrate", help="bring a pre-0.2 data folder into the workspace; the old files are left as they are")
    p.add_argument("--from", dest="source", help="the old data folder (default: the data folder itself)")
    p.add_argument("--upload", action="store_true", help="also send the old raw .fit/.gpx files and hand-logged sessions to the provider")
    p.add_argument("--force", action="store_true", help="replace a plan the workspace already has")
    p.set_defaults(run=cmd_migrate)
    p = sub.add_parser("doctor", help="check the provider, the key, the harness and the sandbox")
    p.add_argument("--write", action="store_true", help="also create and remove a test workout on the provider's calendar")
    p.set_defaults(run=cmd_doctor)
    return parser


def main() -> None:
    parser = build()
    arguments = parser.parse_args()
    if arguments.command is None:
        return cmd_serve(arguments)
    arguments.run(arguments)


if __name__ == "__main__":
    main()
