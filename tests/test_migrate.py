import json
import shutil
from pathlib import Path

from app import storage
from app.migrate import migrate


def test_v1_plans_move_to_data_as_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    source = tmp_path / "plans"
    source.mkdir()
    shutil.copy("examples/sample_plan.json", source / "2026-10-31T09-00-00_hm.json")

    assert migrate(source) == ["2026-10-31T09-00-00_hm"]
    assert migrate(source) == []
    assert not list(source.glob("*.json"))

    plan = storage.load_plan("2026-10-31T09-00-00_hm")
    assert plan.schema_version == "2.0"
    assert plan.meta.status == "draft" and plan.meta.version == 1
    original = json.loads(Path("examples/sample_plan.json").read_text())
    assert len(plan.plan.weeks) == len(original["plan"]["weeks"])
