"""What an agent can ask the server to do, behind the `openstride` CLI. Each takes the caller's role.

These are the only doors out of the workspace: a card into the stream, a draft in front of the athlete,
the provider, the analyst. None of them writes the plan.
"""
from datetime import date
from pathlib import Path

from app import analyst, cards, clock, hub as hub_module, mirror, plan, providers, workspace
from app.hub import hub
from app.providers import ProviderError

UPLOAD_SUFFIXES = (".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz", ".zip")
SPORTS = ("run", "strength", "ride", "swim", "walk", "hike", "row", "ski", "other")


class OpError(Exception):
    """A request that can't be done, with a message the agent can act on."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


def show(role: str, card: str, args: dict | None = None, data: dict | None = None) -> dict:
    try:
        built = cards.build(card, args, data)
    except cards.CardError as error:
        raise OpError(str(error)) from error
    static = card in cards.STATIC
    event = hub.stream.append("card", card=card, args=args or {}, data=built if static else None, by=role)
    return {"shown": card, "event": event["id"]}


def ask(role: str, question: str, options: list[str]) -> dict:
    return show(role, "choice", data={"question": question, "options": options})


def draft(role: str, payload: dict, new: bool = False) -> dict:
    try:
        proposal = plan.draft(payload, new=new, author="analyst" if role == "analyst" else "agent")
    except plan.PlanError as error:
        raise OpError(str(error), error.problems) from error
    hub.stream.append("proposal", proposal_id=proposal.id, by=role)
    changed = plan.diff(None if proposal.kind == "new_plan" else plan.load(), proposal.doc)
    return {
        "proposal": proposal.id, "kind": proposal.kind, "warnings": proposal.warnings,
        "weeks_changed": [week["week_number"] for week in changed],
        "next": "The athlete now sees this with Approve and Reject. Explain it in your reply; the plan has not changed.",
    }


def provider_call(action):
    try:
        return action()
    except ProviderError as error:
        raise OpError(str(error)) from error


def source_capabilities(role: str) -> dict:
    provider = providers.active()
    if provider is None:
        return {"provider": None, "connected": False, "capabilities": {}}
    return {"provider": provider.name, "connected": True, "capabilities": provider.capabilities.as_dict()}


def source_check(role: str) -> dict:
    provider = provider_call(providers.require)
    return provider_call(provider.check) | {"provider": provider.name}


def source_sync(role: str, since: str | None = None) -> dict:
    result = provider_call(lambda: mirror.sync(date.fromisoformat(since) if since else None))
    doc = plan.load()
    if doc is not None:
        try:
            result["calendar_changes"] = mirror.drift()
        except ProviderError as error:
            result["notes"].append(f"The calendar was not checked: {error}")
    hub.stream.publish("data_changed")
    return result


def source_streams(role: str, reference: str) -> dict:
    try:
        data = provider_call(lambda: mirror.streams(reference))
        row = mirror.find(reference)
    except mirror.NotFound as error:
        raise OpError(str(error)) from error
    columns = data.get("columns") or {}
    return {
        "path": f"activities/{row['key']}.streams.json", "columns": {name: len(values) for name, values in columns.items()},
        "note": "One value per sample in every column. Read it with Python or jq, not by eye.",
    }


def source_push(role: str, force: bool = False) -> dict:
    doc = plan.load()
    if doc is None:
        raise OpError("There is no plan to send.")
    return provider_call(lambda: mirror.push_calendar(doc, force=force))


def source_upload(role: str, path: str) -> dict:
    provider = provider_call(providers.require)
    if not provider.capabilities.upload:
        raise OpError(f"{provider.name} can't take files. The activity has to reach it another way.")
    rel = workspace.relative(path)
    if rel is None or not (workspace.root() / rel).is_file():
        raise OpError("Give a file inside the workspace; uploads from the athlete are under scratch/uploads/.")
    file = workspace.root() / rel
    if not file.name.lower().endswith(UPLOAD_SUFFIXES):
        raise OpError("Only .fit, .gpx and .tcx recordings go to the provider.")
    ids = provider_call(lambda: provider.upload(file.name, file.read_bytes()))
    return {"uploaded": ids} | source_sync(role)


def source_wellness(role: str, day: str, fields: dict) -> dict:
    provider = provider_call(providers.require)
    if not provider.capabilities.wellness_write:
        raise OpError(f"{provider.name} doesn't take wellness notes. Write it in the journal instead.")
    try:
        when = clock.today() if day in ("", "today") else date.fromisoformat(day)
    except ValueError as error:
        raise OpError("The date must be YYYY-MM-DD or today.") from error
    provider_call(lambda: provider.put_wellness(when, fields))
    return {"written": sorted(fields), "date": when.isoformat()} | {"sync": source_sync(role, when.isoformat())["wellness_days"]}


def source_log(role: str, entry: dict) -> dict:
    """A session with no recording, logged at the athlete's word."""
    provider = provider_call(providers.require)
    if not provider.capabilities.manual:
        raise OpError(f"{provider.name} can't log a session by hand. Note it in the journal instead.")
    if entry.get("sport") not in SPORTS:
        raise OpError(f"sport must be one of {', '.join(SPORTS)}.")
    try:
        when = clock.today() if entry.get("date") in (None, "", "today") else date.fromisoformat(entry["date"])
        minutes = float(entry["minutes"])
    except (KeyError, TypeError, ValueError) as error:
        raise OpError("A logged session needs a date (YYYY-MM-DD or today) and minutes.") from error
    if when > clock.today() or minutes <= 0:
        raise OpError("A logged session is something that happened: not in the future, and longer than zero minutes.")
    ident = provider_call(lambda: provider.log_manual(entry | {"date": when.isoformat(), "minutes": minutes}))
    return {"logged": ident} | source_sync(role, when.isoformat())


def commit(role: str, message: str) -> dict:
    if hub.agent is not None:
        hub.agent.commit_message = " ".join(message.split())[:120]
    return {"ok": True, "note": "The workspace is committed with this message when your turn ends."}


def restore(role: str, path: str, revision: str) -> dict:
    """Put a file the agent authored back as it was in an earlier commit."""
    rel = workspace.relative(path)
    if rel is None or workspace.tier(rel) != "authored":
        raise OpError("Only files you author can be restored this way. The plan changes through a proposal; `openstride plan export --version` gives you an old one to draft from.")
    try:
        content = workspace.show(revision, rel)
    except workspace.GitError as error:
        raise OpError(f"{rel} is not in commit {revision}.") from error
    workspace.write_file(workspace.root() / rel, content)
    return {"restored": rel, "from": revision}


def reports(role: str) -> dict:
    return {"reports": analyst.reports()}


def usage(role: str) -> dict:
    return hub_module.usage() | {"monthly_cap_usd": workspace.settings()["budget"]["monthly_usd"]}


def save_upload(name: str, content: bytes) -> str:
    """A file the athlete dropped into the chat, kept where the agent can read it. Returns its workspace path."""
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in Path(name).name)[-80:] or "upload"
    folder = workspace.root() / "scratch" / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{clock.now():%Y%m%d-%H%M%S}_{safe}"
    workspace.write_file(target, content)
    return target.relative_to(workspace.root()).as_posix()
