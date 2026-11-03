"""The coach's notes about the athlete: one Markdown file that outlives plans and cleared chats.

Only the coach (through its tool) and the athlete (through the app) write it. Code never does: facts the
app already holds stay in the plan, activity and metrics files, and the notes hold what those can't say.
"""
import json
from datetime import datetime, timezone

from app import storage

MAX_CHARS = 12000  # about 3k tokens: a detailed profile, not a second transcript
TEMPLATE = """# Profile
(age range, years running, races and times worth knowing, typical weekly volume over the years, other sports)

# Goals
(this plan's goal and what sits behind it, longer-term goals, what a good outcome looks like to them)

# Life and schedule
(work, family, travel patterns, sleep, usual training times, days and hours that are hard, seasons and weather)

# Injury and health
(dated: injuries, niggles and illness, how they resolved, what tends to trigger them; anything medical that matters for training)

# Response to training
(dated observations: the volume and intensity they handle, how they recover, early warning signs, what has worked and what hasn't)

# Preferences and agreements
(how they like to train and be coached, things they asked for that should hold across plans, how direct to be)

# Plan history
(one entry per plan: dates, goal, what happened, the race result, the lesson)

# Open questions
(what you still want to find out and anything to follow up on; ask one or two at a time, when they fit)
"""


def folder():
    return storage.DATA_DIR / "athlete"


def path():
    return folder() / "memory.md"


def load() -> str:
    return path().read_text() if path().exists() else ""


def for_prompt() -> str:
    notes = load() or "Nothing written yet; the template below is what the file should look like.\n\n" + TEMPLATE
    return f"Your notes about the athlete, kept across plans (coach's notes, not app data):\n{notes}"


def versions() -> list[str]:
    """Timestamps of earlier versions, newest first."""
    return sorted((p.stem for p in (folder() / "versions").glob("*.md")), reverse=True)


def load_version(stamp: str) -> str:
    file = folder() / "versions" / f"{storage.safe_id(stamp)}.md"
    if not file.exists():
        raise storage.NotFound(stamp)
    return file.read_text()


def save(content: str, by: str) -> dict:
    """Replace the notes. The previous version is kept, so a bad rewrite is recoverable."""
    content = content.strip() + "\n"
    if len(content) > MAX_CHARS:
        raise ValueError(f"The notes are {len(content)} characters; the limit is {MAX_CHARS}. Condense them.")
    folder().mkdir(parents=True, exist_ok=True)
    with storage.file_lock(folder() / ".lock"):
        old = load()
        if old and old != content:
            stamp = f"{datetime.now(timezone.utc):%Y-%m-%dT%H-%M-%S}"
            stamp = storage.free_name(stamp, lambda name: (folder() / "versions" / f"{name}.md").exists(), "_")
            storage.write_file(folder() / "versions" / f"{stamp}.md", old)
        storage.write_file(path(), content)
        storage.write_json(folder() / "memory_meta.json", {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "updated_by": by})
    return status()


def status() -> dict:
    meta_path = folder() / "memory_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"updated_at": "", "updated_by": ""}
    return {"content": load(), "template": TEMPLATE, "max_chars": MAX_CHARS, "versions": versions()} | meta


def restore(stamp: str) -> dict:
    return save(load_version(stamp), by="athlete")


