"""JSON files on disk. All ids pass through `safe_id`: they arrive in URLs and uploads.

Phone and laptop can write at once, so a read-modify-write runs under a lock. The order is
always activation_lock, then plan_lock, then activities_lock, and locks never wrap an LLM call.
"""
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from pydantic import TypeAdapter

from app.config import settings
from app.models import ActivitySummary, ChatMessage, ManualActivity, PlanFile, Proposal, ReviewFile

DATA_DIR = settings.data_dir
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
LOCK_TIMEOUT_S = 30  # locks cover file writes only, so waiting this long means something is stuck


CHAT_THREAD = TypeAdapter(list[ChatMessage])


class NotFound(Exception):
    pass


def safe_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise NotFound(value)
    return value


def write_file(path: Path, content: str | bytes, mode: int = 0o666) -> None:
    """Replace atomically: a crash mid-write must not destroy the current plan.

    The temp name is unique per write, and doesn't end in .json so listings never see it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        kind = "xb" if isinstance(content, bytes) else "x"
        with open(temporary, kind, opener=lambda name, flags: os.open(name, flags, mode)) as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path: Path, text: str) -> None:
    write_file(path, text)


held_locks = threading.local()


@contextmanager
def file_lock(path: Path):
    """FileLock objects on one path block each other even in one thread, so nesting is tracked here."""
    held = held_locks.__dict__.setdefault("paths", set())
    if path in held:
        yield
        return
    with FileLock(path, timeout=LOCK_TIMEOUT_S):
        held.add(path)
        try:
            yield
        finally:
            held.discard(path)


def activation_lock():
    """Choosing the active plan, which has to see and change two plans as one step."""
    (DATA_DIR / "plans").mkdir(parents=True, exist_ok=True)
    return file_lock(DATA_DIR / "plans" / ".lock")


def plan_lock(plan_id: str):
    folder = plan_dir(plan_id)
    if not folder.is_dir():
        raise NotFound(plan_id)
    path = folder / ".lock"
    held = held_locks.__dict__.get("paths", set())
    if activities_lock_path() in held and path not in held:
        raise RuntimeError("plan_lock must be taken before activities_lock")
    return file_lock(path)


def activities_lock_path() -> Path:
    return DATA_DIR / "activities" / ".lock"


def activities_lock():
    """Everything under data/activities/, and manual entries that belong to no plan."""
    activities_lock_path().parent.mkdir(parents=True, exist_ok=True)
    return file_lock(activities_lock_path())


@contextmanager
def workout_lock(plan_id: str):
    """A change to a workout: its plan, if it has one, then the activities."""
    with plan_lock(plan_id) if plan_id else nullcontext(), activities_lock():
        yield


def plan_etag(plan_id: str) -> str:
    """Taken from one open file, so the version and mtime can't come from two different writes."""
    try:
        with open(plan_dir(plan_id) / "plan.json") as file:
            mtime = os.fstat(file.fileno()).st_mtime_ns
            return f'"{json.load(file)["meta"]["version"]}-{mtime}"'
    except FileNotFoundError:
        raise NotFound(plan_id) from None


def data_dir_writable() -> bool:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=DATA_DIR):
            return True
    except OSError:
        return False


def plan_dir(plan_id: str) -> Path:
    return DATA_DIR / "plans" / safe_id(plan_id)


def new_plan_id(base: str) -> str:
    """Claims the folder too, so two plans made in the same second can't share an id."""
    plan_id, suffix = base, 1
    (DATA_DIR / "plans").mkdir(parents=True, exist_ok=True)
    while True:
        try:
            plan_dir(plan_id).mkdir()
            return plan_id
        except FileExistsError:
            suffix += 1
            plan_id = f"{base}-{suffix}"


def save_plan(plan_file: PlanFile) -> None:
    write_json(plan_dir(plan_file.meta.id) / "plan.json", plan_file.model_dump_json(indent=2))


def load_plan(plan_id: str) -> PlanFile | None:
    try:
        path = plan_dir(plan_id) / "plan.json"
    except NotFound:
        return None
    return PlanFile.model_validate_json(path.read_text()) if path.exists() else None


# TODO(v4.1): listings can raise FileNotFoundError if another device deletes a file mid-listing; a reload fixes it.
def list_plans() -> list[dict]:
    plans = []
    for path in sorted((DATA_DIR / "plans").glob("*/plan.json"), reverse=True):  # ids start with a timestamp
        data = json.loads(path.read_text())
        plans.append({
            "id": data["meta"]["id"],
            "created_at": data["meta"]["created_at"],
            "goal_description": data["request"]["goal_description"],
            "race_date": data["request"]["race_date"],
            "status": data["meta"].get("status", "draft"),
            "version": data["meta"].get("version", 1),
        })
    return plans


def series_path(activity_id: str) -> Path:
    return DATA_DIR / "activities" / f"{safe_id(activity_id)}.series.json"


def has_series(activity_id: str) -> bool:
    return series_path(activity_id).exists()


def save_series(series: dict) -> None:
    write_json(series_path(series["activity_id"]), json.dumps(series))


def load_series(activity_id: str) -> dict:
    path = series_path(activity_id)
    if not path.exists():
        raise NotFound(activity_id)
    return json.loads(path.read_text())


def save_raw(activity_id: str, name: str, content: bytes) -> None:
    suffix = "fit" if name.lower().endswith(".fit") else "gpx"
    write_file(DATA_DIR / "activities" / "raw" / f"{safe_id(activity_id)}.{suffix}", content)


def find_raw(activity_id: str) -> Path | None:
    return next((DATA_DIR / "activities" / "raw").glob(f"{safe_id(activity_id)}.*"), None)


def delete_activity(activity_id: str) -> None:
    """The activity and everything stored with it."""
    raw = find_raw(activity_id)
    for path in (activity_path(activity_id), review_path(activity_id), series_path(activity_id), raw):
        if path:
            path.unlink(missing_ok=True)


def activity_path(activity_id: str) -> Path:
    return DATA_DIR / "activities" / f"{safe_id(activity_id)}.json"


def new_activity_id(base: str) -> str:
    activity_id, suffix = base, 1
    while activity_path(activity_id).exists():
        suffix += 1
        activity_id = f"{base}_{suffix}"
    return activity_id


def save_activity(activity: ActivitySummary) -> None:
    write_json(activity_path(activity.id), activity.model_dump_json(indent=2))


def load_activity(activity_id: str) -> ActivitySummary:
    path = activity_path(activity_id)
    if not path.exists():
        raise NotFound(activity_id)
    return ActivitySummary.model_validate_json(path.read_text())


def list_all_activities() -> list[ActivitySummary]:
    return [
        ActivitySummary.model_validate_json(path.read_text())
        for path in sorted((DATA_DIR / "activities").glob("*.json"))  # ids start with the start time
        if not path.name.endswith(".series.json")
    ]


def list_activities(plan_id: str) -> list[ActivitySummary]:
    return [activity for activity in list_all_activities() if activity.plan_id == plan_id]


def review_path(activity_id: str) -> Path:
    return DATA_DIR / "reviews" / f"{safe_id(activity_id)}.json"


def save_review(review_file: ReviewFile) -> None:
    write_json(review_path(review_file.activity_id), review_file.model_dump_json(indent=2))


def load_review(activity_id: str) -> ReviewFile | None:
    path = review_path(activity_id)
    return ReviewFile.model_validate_json(path.read_text()) if path.exists() else None


def new_proposal_id(plan_id: str, base: str) -> str:
    proposal_id, suffix = base, 1
    while (plan_dir(plan_id) / "proposals" / f"{proposal_id}.json").exists():
        suffix += 1
        proposal_id = f"{base}-{suffix}"
    return proposal_id


def manual_dir(plan_id: str) -> Path:
    """Logged with no active plan, an entry has plan_id "" and sits outside any plan."""
    return plan_dir(plan_id) / "manual_activities" if plan_id else DATA_DIR / "manual_activities"


def new_manual_id(plan_id: str, day) -> str:
    number = 1
    while (manual_dir(plan_id) / f"manual_{day}_{number}.json").exists():
        number += 1
    return f"manual_{day}_{number}"


def save_manual(entry: ManualActivity) -> None:
    write_json(manual_dir(entry.plan_id) / f"{safe_id(entry.id)}.json", entry.model_dump_json(indent=2))


def manual_paths(name: str = "*") -> list[Path]:
    return [*(DATA_DIR / "plans").glob(f"*/manual_activities/{name}.json"), *(DATA_DIR / "manual_activities").glob(f"{name}.json")]


def load_manual(manual_id: str) -> ManualActivity:
    for path in manual_paths(safe_id(manual_id)):
        return ManualActivity.model_validate_json(path.read_text())
    raise NotFound(manual_id)


def list_all_manual() -> list[ManualActivity]:
    return [ManualActivity.model_validate_json(path.read_text()) for path in sorted(manual_paths())]


def list_manual(plan_id: str) -> list[ManualActivity]:
    paths = sorted(manual_dir(plan_id).glob("*.json"))
    return [ManualActivity.model_validate_json(path.read_text()) for path in paths]


def delete_manual(entry: ManualActivity) -> None:
    (manual_dir(entry.plan_id) / f"{safe_id(entry.id)}.json").unlink()


def load_chat(plan_id: str) -> list[ChatMessage]:
    path = plan_dir(plan_id) / "chat.json"
    return CHAT_THREAD.validate_json(path.read_text()) if path.exists() else []


def save_chat(plan_id: str, messages: list[ChatMessage]) -> None:
    write_json(plan_dir(plan_id) / "chat.json", CHAT_THREAD.dump_json(messages, indent=2).decode())


def archive_chat(plan_id: str, stamp: str) -> None:
    path = plan_dir(plan_id) / "chat.json"
    if path.exists():
        os.replace(path, plan_dir(plan_id) / f"chat_{stamp}.json")


def all_chat_messages(plan_id: str) -> list[ChatMessage]:
    """The live thread and every cleared one."""
    paths = plan_dir(plan_id).glob("chat*.json")
    return [message for path in paths for message in CHAT_THREAD.validate_json(path.read_text())]


def save_proposal(proposal: Proposal) -> None:
    path = plan_dir(proposal.plan_id) / "proposals" / f"{safe_id(proposal.proposal_id)}.json"
    write_json(path, proposal.model_dump_json(indent=2))


def load_proposal(proposal_id: str) -> Proposal:
    # Proposal routes carry no plan id.
    for path in (DATA_DIR / "plans").glob(f"*/proposals/{safe_id(proposal_id)}.json"):
        return Proposal.model_validate_json(path.read_text())
    raise NotFound(proposal_id)


def list_proposals(plan_id: str) -> list[Proposal]:
    paths = sorted((plan_dir(plan_id) / "proposals").glob("*.json"), reverse=True)
    return [Proposal.model_validate_json(path.read_text()) for path in paths]


def load_version(plan_id: str, version: int) -> PlanFile:
    path = plan_dir(plan_id) / "versions" / f"plan_v{version}.json"
    if not path.exists():
        raise NotFound(str(version))
    return PlanFile.model_validate_json(path.read_text())


def list_versions(plan_id: str) -> list[PlanFile]:
    paths = (plan_dir(plan_id) / "versions").glob("plan_v*.json")
    return sorted((PlanFile.model_validate_json(p.read_text()) for p in paths), key=lambda p: p.meta.version)
