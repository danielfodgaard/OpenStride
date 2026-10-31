"""JSON files on disk. All ids pass through `safe_id`: they arrive in URLs and uploads."""
import json
import os
import re
from pathlib import Path

from app.models import ActivitySummary, PlanFile, Proposal, ReviewFile

DATA_DIR = Path(os.environ.get("OPENSTRIDE_DATA_DIR") or Path(__file__).parent.parent / "data")
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class NotFound(Exception):
    pass


def safe_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise NotFound(value)
    return value


def write_json(path: Path, text: str) -> None:
    # Replace atomically: a crash mid-write must not destroy the current plan.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def plan_dir(plan_id: str) -> Path:
    return DATA_DIR / "plans" / safe_id(plan_id)


def new_plan_id(base: str) -> str:
    plan_id, suffix = base, 1
    while plan_dir(plan_id).exists():
        suffix += 1
        plan_id = f"{base}-{suffix}"
    return plan_id


def save_plan(plan_file: PlanFile) -> None:
    write_json(plan_dir(plan_file.meta.id) / "plan.json", plan_file.model_dump_json(indent=2))


def load_plan(plan_id: str) -> PlanFile | None:
    try:
        path = plan_dir(plan_id) / "plan.json"
    except NotFound:
        return None
    return PlanFile.model_validate_json(path.read_text()) if path.exists() else None


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


def activity_path(activity_id: str) -> Path:
    return DATA_DIR / "activities" / f"{safe_id(activity_id)}.json"


def save_activity(activity: ActivitySummary) -> None:
    write_json(activity_path(activity.id), activity.model_dump_json(indent=2))


def load_activity(activity_id: str) -> ActivitySummary:
    path = activity_path(activity_id)
    if not path.exists():
        raise NotFound(activity_id)
    return ActivitySummary.model_validate_json(path.read_text())


def list_activities(plan_id: str) -> list[ActivitySummary]:
    activities = [
        ActivitySummary.model_validate_json(path.read_text())
        for path in sorted((DATA_DIR / "activities").glob("*.json"))  # ids start with the start time
    ]
    return [activity for activity in activities if activity.plan_id == plan_id]


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
