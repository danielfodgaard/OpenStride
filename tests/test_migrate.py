"""Bringing a pre-0.2 data folder into the workspace, without touching it."""
import hashlib
import shutil
from pathlib import Path

from app import migrate, plan, workspace
from tests.conftest import ROOT

LEGACY = ROOT / "examples" / "demo" / "data"


def fingerprint(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_the_plan_the_notes_and_the_history_come_over_and_the_old_files_stay(ws, tmp_path):
    old = tmp_path / "old"
    shutil.copytree(LEGACY, old)
    (old / "athlete").mkdir()
    (old / "athlete" / "memory.md").write_text("# Profile\nAchilles niggle in spring.\n")
    before = fingerprint(old)
    report = migrate.run(old)
    assert fingerprint(old) == before
    doc = plan.load()
    assert len(doc.plan.weeks) == 10 and doc.request.runs_per_week == 4
    ids = [s.id for s in doc.plan.sessions()]
    assert len(ids) == len(set(ids)) and all(ids)
    assert (workspace.root() / "athlete" / "memory.md").read_text().endswith("Achilles niggle in spring.\n")
    legacy = workspace.root() / "journal" / "legacy"
    assert "Legs heavy, slept badly" in (legacy / "reviews.md").read_text()
    assert "Strength B" in (legacy / "logbook.md").read_text()
    assert "Is my easy pace too fast?" in (legacy / "chat.md").read_text()
    assert workspace.log("plan/plan.json")[0]["message"].startswith("Plan (imported version 1)")
    assert workspace.changed(["plan", "journal", "athlete"]) == []
    assert any("left exactly as they were" in line for line in report)


def test_running_it_again_keeps_the_plan_the_workspace_has(ws):
    migrate.run(LEGACY)
    first = plan.plan_commit()
    report = migrate.run(LEGACY)
    assert plan.plan_commit() == first and any("already has one" in line for line in report)


def test_every_old_version_becomes_a_commit(ws, tmp_path):
    import json

    old = tmp_path / "old"
    shutil.copytree(LEGACY, old)
    plan_dir = next((old / "plans").iterdir())
    current = json.loads((plan_dir / "plan.json").read_text())
    current["meta"]["version"] = 2
    current["meta"]["history"] = [{"version": 2, "proposal_id": "", "accepted_at": "2026-11-12T10:00:00+00:00", "rationale": "", "origin": "manual", "summary": "Moved Tue easy 9 km to Wed"}]
    current["plan"]["weeks"][5]["sessions"][0]["title"] = "Changed by hand"
    (plan_dir / "plan.json").write_text(json.dumps(current))
    migrate.run(old)
    messages = [entry["message"] for entry in plan.history()]
    assert messages == ["Plan (imported version 2): Moved Tue easy 9 km to Wed", "Plan (imported version 1): accepted"]
    assert plan.load().plan.weeks[5].sessions[0].title == "Changed by hand"


def test_a_folder_with_nothing_old_in_it(ws, tmp_path):
    assert migrate.run(tmp_path)[0].startswith("Nothing to migrate")
