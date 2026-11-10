"""The analyst: a second agent that computes. It runs in its own folder with the workspace read-only,
and hands back a report, cards and, when the question was about the plan, a proposal.

It never writes the workspace: when a run ends, the server files its report/ folder under reports/.
"""
import asyncio
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app import clock, config, hub as hub_module, spool, workspace
from app.agent import system_prompt
from app.runtime.base import TurnRequest
from app.runtime.hooks import Policy

log = logging.getLogger(__name__)
MAX_SECONDS = 1800
MAX_FILE_BYTES = 5_000_000
KEEP = (".md", ".png", ".svg", ".json", ".csv", ".py", ".txt")

_one_at_a_time = asyncio.Semaphore(1)
_tasks: dict[str, asyncio.Task] = {}
_waiting: dict[str, int] = {}  # run id -> callers waiting on it right now


def runs_dir() -> Path:
    """Beside the workspace and the server's state, inside neither: the analyst may read and write only here."""
    return config.settings.data_dir / "analyst"


def run_file(run_id: str) -> Path:
    return runs_dir() / run_id / "run.json"


def status(run_id: str) -> dict:
    path = run_file(run_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id) or not path.exists():
        return {"id": run_id, "state": "unknown"}
    return json.loads(path.read_text())


def save(run: dict) -> None:
    workspace.write_file(run_file(run["id"]), json.dumps(run, indent=1))


def new_run(question: str, web: bool, effort: str, context: str = "") -> dict:
    now = datetime.now(timezone.utc)
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40] or "analysis"
    run_id = f"{now:%Y-%m-%dT%H-%M}_{slug}"
    while (runs_dir() / run_id).exists():
        run_id += "x"
    scratch = runs_dir() / run_id
    (scratch / "report").mkdir(parents=True)
    workspace.install_skills(scratch, "analyst")
    run = {"id": run_id, "state": "running", "question": question, "context": context, "web": web, "effort": effort,
           "started_at": workspace.now_iso(), "summary": "", "report": "", "error": ""}
    save(run)
    return run


def file_report(run_id: str) -> str:
    """The analyst's report/ folder, copied under reports/ and committed. Returns the report id, or "" when it wrote none."""
    source = runs_dir() / run_id / "report"
    if not (source / "report.md").exists():
        return ""
    with workspace.lock():
        target = workspace.root() / "reports" / run_id
        for path in sorted(source.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in KEEP and path.stat().st_size <= MAX_FILE_BYTES:
                destination = target / path.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
        title = next((line.lstrip("# ").strip() for line in (source / "report.md").read_text().splitlines() if line.strip()), run_id)
        workspace.commit_protected(f"Report: {title[:80]}", ["reports"])
    return run_id


async def execute(hub, run: dict) -> dict:
    scratch = runs_dir() / run["id"]
    settings = workspace.settings()
    policy = Policy(
        role="analyst", workspace=workspace.root(), cwd=scratch, scratch=scratch, web=bool(run["web"]),
        secrets=(str(workspace.state()), "approval_key", "session_secret"),
    )
    token = hub.token_for("analyst")
    today = clock.today()
    prompt = "\n".join([
        f"Today is {today:%A} {today.isoformat()}.",
        f"The workspace is at {workspace.root()} (read-only for you). Your own folder is the current directory.",
        "", "The question:", run["question"],
        *((["", "Context from the conversation:", run["context"]]) if run["context"] else []),
    ])
    request = TurnRequest(
        prompt=prompt, system_prompt=system_prompt("analyst"), policy=policy, model=settings["models"]["analyst"],
        helper_model=settings["models"]["helper"], effort=run["effort"] or settings["effort"]["analyst"], token=token,
        env={"OPENSTRIDE_SPOOL": str(scratch / ".rpc")}, max_seconds=MAX_SECONDS,
    )
    texts, result = [], {}
    async with _one_at_a_time:
        spool.register(scratch / ".rpc")
        try:
            async for kind, payload in hub.runtime.run(request):
                if kind == "text":
                    texts.append(payload.strip())
                elif kind == "tool":
                    hub.stream.publish("status", text="The analyst is working on it…")
                elif kind == "result":
                    result = payload
        finally:
            spool.unregister(scratch / ".rpc")
            hub.tokens.pop(token, None)
    hub_module.record_usage(role="analyst", turn=run["id"], origin="analyst", model=request.model, cost_usd=result.get("cost_usd") or 0, usage=result.get("usage") or {})
    run = status(run["id"]) | {"finished_at": workspace.now_iso(), "summary": texts[-1] if texts else "", "error": result.get("error") or ""}
    try:
        run["report"] = await asyncio.to_thread(file_report, run["id"])
    except Exception as error:
        log.exception("Filing the analyst's report failed")
        run["error"] = run["error"] or f"The report could not be filed: {error}"
    run["state"] = "failed" if run["error"] and not run["report"] else "done"
    save(run)
    workspace.sweep(scratch)
    hub.stream.publish("data_changed")
    return run


def start(hub, question: str, web: bool = False, effort: str = "", context: str = "") -> dict:
    """Begin a run on the server's loop. Call from the loop."""
    run = new_run(question, web, effort, context)

    async def runner():
        finished = await execute(hub, run)
        if not _waiting.get(run["id"]) and hub.agent is not None:
            # Nobody was waiting any more: tell the coach, so the athlete hears what was found.
            where = f" The report is reports/{finished['report']}/report.md." if finished["report"] else ""
            hub.agent.enqueue("brief", f"The analyst finished “{run['question']}”.{where}\n\nIts summary:\n{finished['summary'] or finished['error']}", "analyst_done")

    _tasks[run["id"]] = asyncio.create_task(runner())
    return run


async def wait(run_id: str, seconds: float) -> dict:
    """The run's state after waiting up to `seconds` for it to finish."""
    task = _tasks.get(run_id)
    if task is not None and not task.done():
        _waiting[run_id] = _waiting.get(run_id, 0) + 1
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
        except TimeoutError:
            pass
        finally:
            _waiting[run_id] -= 1
    return status(run_id)


def reports() -> list[dict]:
    found = []
    for folder in sorted((workspace.root() / "reports").glob("*/"), reverse=True):
        report = folder / "report.md"
        if report.exists():
            title = next((line.lstrip("# ").strip() for line in report.read_text().splitlines() if line.strip()), folder.name)
            found.append({"id": folder.name, "title": title, "path": f"reports/{folder.name}/report.md"})
    return found
