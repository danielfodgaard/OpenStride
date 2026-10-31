"""Plan status: draft -> active -> archived. Everything that changes a status lives here."""
import shutil
from datetime import datetime, timezone

from pydantic import ValidationError

from app import generator, storage
from app.models import SCHEMA_VERSION, PlanFile, SavedRequest


class Conflict(Exception):
    """The plan's status doesn't allow this action. Maps to HTTP 409."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_plan(plan_id: str) -> PlanFile:
    plan_file = storage.load_plan(plan_id)
    if plan_file is None:
        raise storage.NotFound(plan_id)
    return plan_file


def require_draft(plan_id: str) -> PlanFile:
    plan_file = require_plan(plan_id)
    if plan_file.meta.status != "draft":
        raise Conflict(f"Only drafts can do this; this plan is {plan_file.meta.status}.")
    return plan_file


def require_active(plan_id: str) -> PlanFile:
    plan_file = require_plan(plan_id)
    if plan_file.meta.status != "active":
        raise Conflict(f"Only the active plan can do this; this plan is {plan_file.meta.status}.")
    return plan_file


def active_plan() -> PlanFile | None:
    active = next((p for p in storage.list_plans() if p["status"] == "active"), None)
    return storage.load_plan(active["id"]) if active else None


def write_version(plan_file: PlanFile) -> None:
    path = storage.plan_dir(plan_file.meta.id) / "versions" / f"plan_v{plan_file.meta.version}.json"
    storage.write_json(path, plan_file.model_dump_json(indent=2))


def accept(plan_id: str) -> PlanFile:
    plan_file = require_draft(plan_id)
    previous = active_plan()
    if previous:
        previous.meta.status = "archived"
        storage.save_plan(previous)
    plan_file.schema_version = SCHEMA_VERSION
    plan_file.meta.status = "active"
    plan_file.meta.accepted_at = now_iso()
    plan_file.meta.version = 1
    write_version(plan_file)
    storage.save_plan(plan_file)
    return plan_file


def discard(plan_id: str) -> None:
    require_draft(plan_id)
    shutil.rmtree(storage.plan_dir(plan_id))


def regenerate(plan_id: str, feedback: str, request: SavedRequest | None = None, client=None, today=None) -> PlanFile:
    draft = require_draft(plan_id)
    new = generator.regenerate_plan(draft, feedback, request or draft.request, client, today)
    new.meta.feedback_history = [*draft.meta.feedback_history, feedback]

    versions = storage.plan_dir(plan_id) / "versions"
    number = len(list(versions.glob("draft_v*.json"))) + 1
    storage.write_json(versions / f"draft_v{number}.json", draft.model_dump_json(indent=2))
    storage.save_plan(new)
    return new


def import_plan(content: bytes) -> PlanFile:
    """Any valid plan file becomes a fresh draft. Its id and status come from us, not the file."""
    try:
        plan_file = PlanFile.model_validate_json(content)
    except ValidationError as error:
        raise ValueError(f"Not a valid plan file: {error.error_count()} problem(s), first: {error.errors()[0]['msg']}") from error
    created = datetime.now(timezone.utc)
    plan_file.schema_version = SCHEMA_VERSION
    plan_file.meta.id = storage.new_plan_id(f"{created:%Y-%m-%dT%H-%M-%S}_import")
    plan_file.meta.status = "draft"
    plan_file.meta.version = 1
    plan_file.meta.history = []
    plan_file.meta.accepted_at = ""
    plan_file.meta.feedback_history = []
    storage.save_plan(plan_file)
    return plan_file
