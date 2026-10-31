"""One-off move of v1 plans/<id>.json into data/plans/<id>/plan.json, as drafts.

Run once with `python -m app.migrate`. Safe to re-run. Originals go only after the copy is written.
"""
from pathlib import Path

from app import storage
from app.models import SCHEMA_VERSION, PlanFile

V1_PLANS_DIR = Path(__file__).parent.parent / "plans"


def migrate(source: Path = V1_PLANS_DIR) -> list[str]:
    moved = []
    for path in sorted(source.glob("*.json")):
        plan_file = PlanFile.model_validate_json(path.read_text())
        plan_file.schema_version = SCHEMA_VERSION
        plan_file.meta.status = "draft"
        storage.save_plan(plan_file)
        if storage.load_plan(plan_file.meta.id) == plan_file:
            path.unlink()
            moved.append(plan_file.meta.id)
    return moved


if __name__ == "__main__":
    print("Migrated:", migrate() or "nothing to do")
