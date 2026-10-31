import json
import re
from pathlib import Path

from app.models import PlanFile

PLANS_DIR = Path(__file__).parent.parent / "plans"
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def save_plan(plan_file: PlanFile) -> None:
    (PLANS_DIR / f"{plan_file.meta.id}.json").write_text(plan_file.model_dump_json(indent=2))


def list_plans() -> list[dict]:
    plans = []
    for path in sorted(PLANS_DIR.glob("*.json"), reverse=True):  # ids start with a timestamp
        data = json.loads(path.read_text())
        plans.append({
            "id": data["meta"]["id"],
            "created_at": data["meta"]["created_at"],
            "goal_description": data["request"]["goal_description"],
        })
    return plans


def load_plan(plan_id: str) -> PlanFile | None:
    # The id comes from the URL; the pattern keeps it inside PLANS_DIR.
    if not ID_PATTERN.fullmatch(plan_id):
        return None
    path = PLANS_DIR / f"{plan_id}.json"
    return PlanFile.model_validate_json(path.read_text()) if path.exists() else None
