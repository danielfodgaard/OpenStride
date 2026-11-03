"""The coach's notes: one file for the athlete, read by every prompt, written by the coach and the athlete."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, adapt, generator, lifecycle, main, memory, review, storage
from app.models import PlanFile, SavedRequest
from test_chat_actions import NO_CHANGE, structured
from test_generator import SAMPLE, FakeClient, reply

NOTES = "# About the athlete\nCalf trouble in 2025; keep strides off the day after intervals.\n"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    return tmp_path


def test_save_load_and_versions(data_dir):
    assert memory.load() == "" and memory.status()["versions"] == []
    first = memory.save("# About the athlete\nfirst", by="athlete")
    assert first["content"] == "# About the athlete\nfirst\n" and first["updated_by"] == "athlete"
    assert (data_dir / "athlete" / "memory.md").exists()
    assert memory.save("# About the athlete\nfirst", by="coach")["versions"] == []  # same content, no version
    second = memory.save("# About the athlete\nsecond", by="coach")
    assert len(second["versions"]) == 1 and second["updated_by"] == "coach"
    assert memory.load_version(second["versions"][0]) == "# About the athlete\nfirst\n"
    restored = memory.restore(second["versions"][0])
    assert restored["content"] == "# About the athlete\nfirst\n" and len(restored["versions"]) == 2


def test_too_long_is_refused_and_nothing_changes():
    memory.save("ok", by="athlete")
    with pytest.raises(ValueError, match="Condense"):
        memory.save("x" * (memory.MAX_CHARS + 1), by="coach")
    assert memory.load() == "ok\n"


def test_for_prompt_shows_the_template_until_there_are_notes():
    assert "Nothing written yet" in memory.for_prompt() and "# Plan history" in memory.for_prompt()
    memory.save(NOTES, by="athlete")
    assert memory.for_prompt().endswith(NOTES) and "Nothing written yet" not in memory.for_prompt()


def test_plan_generation_reads_the_notes():
    memory.save(NOTES, by="athlete")
    fake = FakeClient(reply(SAMPLE["plan"]))
    generator.generate_plan(SavedRequest.model_validate(SAMPLE["request"]), fake, date(2026, 10, 31))
    assert "Calf trouble in 2025" in fake.prompts[0]


def test_proposals_and_reviews_read_the_notes():
    memory.save(NOTES, by="athlete")
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    lifecycle.accept("plan")
    fake = FakeClient(structured(NO_CHANGE))
    adapt.create_proposal("plan", "this_week", "", fake, date(2026, 11, 11))
    assert "Calf trouble in 2025" in fake.prompts[0]

    run = activities.parse_file("2026-11-05_easy.gpx", Path("examples/activities/2026-11-05_easy.gpx").read_bytes(), "plan")
    run.match = activities.match_activity(run, storage.load_plan("plan"))
    storage.save_activity(run)
    fake = FakeClient(reply({"completion": "as_planned", "headline": "x", "observations": [], "flags": [], "effort": "as_planned"}))
    review.review_activity(run, fake)
    assert "Calf trouble in 2025" in fake.prompts[0]


def test_routes(monkeypatch):
    client = TestClient(main.app)
    assert client.get("/api/memory").json()["content"] == ""
    assert client.put("/api/memory", json={"content": NOTES}).json()["updated_by"] == "athlete"
    assert client.put("/api/memory", json={"content": "x" * (memory.MAX_CHARS + 1)}).status_code == 422
    stamp = client.put("/api/memory", json={"content": "# About the athlete\nnewer"}).json()["versions"][0]
    assert client.post(f"/api/memory/restore/{stamp}").json()["content"] == NOTES
    assert client.post("/api/memory/restore/2020-01-01T00-00-00").status_code == 404
    assert client.post("/api/memory/restore/not%20a%20stamp").status_code == 404
