"""What a runtime is given for one turn, and what it reports back.

A runtime yields ("delta", text) as words arrive, ("text", text) for each finished piece of the reply,
("tool", {"name", "input"}) for each tool call, ("denied", reason) when the boundary refused one, and
last ("result", {...}) with the session id, usage and any error.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Protocol

from app.runtime.hooks import Policy


@dataclass
class TurnRequest:
    prompt: str
    system_prompt: str
    policy: Policy
    session_id: str | None = None
    model: str = "claude-opus-5-5"
    helper_model: str = "claude-sonnet-5-5"
    effort: str = "high"
    token: str = ""  # proves to the server which agent a CLI call comes from
    env: dict = field(default_factory=dict)
    max_seconds: int = 900


class Runtime(Protocol):
    name: str

    def run(self, request: TurnRequest) -> AsyncIterator[tuple[str, object]]: ...


STATUS_BY_COMMAND = (
    (r"openstride show", "Preparing something to show you…"),
    (r"openstride ask", "Preparing a question…"),
    (r"openstride plan draft", "Preparing a plan change…"),
    (r"openstride plan (validate|diff|export)", "Checking the plan…"),
    (r"openstride source (sync|check)", "Syncing your activities…"),
    (r"openstride source (push|upload|wellness|log)", "Updating your training log…"),
    (r"openstride source streams", "Reading a recording…"),
    (r"openstride analyse", "The analyst is working on it…"),
    (r"openstride (status|activities)", "Looking at your training…"),
    (r"openstride (paces|dates)", "Working out the numbers…"),
)
STATUS_BY_PATH = (
    ("plan/", "Reading the plan…"), ("proposals/", "Reading a proposal…"), ("activities/", "Reading your runs…"),
    ("wellness/", "Reading your wellness data…"), ("athlete/memory.md", "Reading my notes about you…"),
    ("athlete/", "Reading your profile…"), ("journal/", "Reading the journal…"), ("notes/", "Reading a review…"),
    ("reports/", "Reading a report…"), ("GUIDE.md", "Checking the guide…"),
)
WRITE_STATUS = (
    ("athlete/memory.md", "Updating my notes about you…"), ("journal/", "Writing the journal…"),
    ("notes/", "Writing a review…"), ("settings.yaml", "Changing a setting…"),
)


def describe(tool: str, tool_input: dict, workspace: Path) -> str:
    """What the athlete is told the wait is for. Never the command itself."""
    if tool == "Bash":
        command = tool_input.get("command") or ""
        return next((text for pattern, text in STATUS_BY_COMMAND if re.search(pattern, command)), "Working with your data…")
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    try:
        path = Path(path).resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        pass
    if tool in ("Write", "Edit"):
        return next((text for prefix, text in WRITE_STATUS if path.startswith(prefix)), "Writing…")
    if tool == "Read":
        return next((text for prefix, text in STATUS_BY_PATH if path.startswith(prefix)), "Reading…")
    if tool == "Skill":
        return "Following a guide…"
    if tool in ("WebSearch", "WebFetch"):
        return "Looking it up…"
    return "Searching your data…" if tool in ("Glob", "Grep") else "Working…"
