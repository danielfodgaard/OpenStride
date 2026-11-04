import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import activities, generator, lifecycle, main, review, storage
from app.models import PlanFile

SAMPLES = Path("examples/activities")
REVIEW = {
    "completion": "as_planned",
    "headline": "Good controlled run",
    "observations": ["Even splits"],
    "flags": [],
    "effort": "as_planned",
}


def reply(data=REVIEW, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json.dumps(data))],
        usage=SimpleNamespace(input_tokens=50, output_tokens=20),
    )


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return reply()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    lifecycle.accept("plan")
    messages = FakeMessages()
    monkeypatch.setattr(generator, "make_client", lambda: SimpleNamespace(messages=messages))
    return messages


def upload(client, name, **data):
    return client.post("/api/activities", files=[("files", (name, (SAMPLES / name).read_bytes()))], data=data).json()[0]


@pytest.fixture
def client():
    return TestClient(main.app)


def test_import_triggers_a_review_with_full_context(client, fake):
    upload(client, "2026-11-05_easy.gpx")
    upload(client, "2026-11-07_easy_no_hr.gpx", user_note="legs heavy", rpe="7")

    prompt = fake.calls[1]["messages"][0]["content"]
    assert "Easy 9 km" in prompt and "legs heavy" in prompt
    assert "Athlete's reference paces" in prompt and "5:11-6:13/km" in prompt
    assert "2026-11-05_easy.gpx" in prompt.split("Previous 7 days")[1]
    assert fake.calls[0]["model"] == "claude-opus-5-5"

    listed = client.get("/api/activities", params={"plan_id": "plan"}).json()
    assert listed[0]["review"]["review"]["headline"] == "Good controlled run"
    assert listed[0]["review"]["meta"]["usage"] == {"input_tokens": 50, "output_tokens": 20, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def test_unplanned_run_says_so(client, fake):
    upload(client, "2026-11-06_shakeout_unplanned.gpx")
    assert "Planned session:\nUnplanned run" in fake.calls[0]["messages"][0]["content"]


def test_previous_week_only_goes_back_seven_days(fake):
    activity = activities.parse_file("a.gpx", (SAMPLES / "2026-11-05_easy.gpx").read_bytes(), "plan")
    older = activity.model_copy(update={"id": "old", "date": date(2026, 10, 28), "start_time": activity.start_time.replace(month=10, day=28)})
    edge = activity.model_copy(update={"id": "edge", "date": date(2026, 10, 29), "start_time": activity.start_time.replace(month=10, day=29)})
    later = activity.model_copy(update={"id": "later", "date": date(2026, 11, 6), "start_time": activity.start_time.replace(day=6)})
    for item in (older, edge, later, activity):
        storage.save_activity(item)
    assert [a.id for a in review.recent_activities(activity)] == ["edge"]


def test_failed_review_keeps_the_run(client, fake, monkeypatch):
    def refuse(**kwargs):
        return reply(stop_reason="refusal")

    monkeypatch.setattr(fake, "create", refuse)
    result = upload(client, "2026-11-05_easy.gpx")
    assert result["status"] == "imported" and "review failed" in result["message"]
    listed = client.get("/api/activities", params={"plan_id": "plan"}).json()
    assert len(listed) == 1 and listed[0]["review"] is None


def test_rerun_review_and_read_only_after_archiving(client, fake):
    upload(client, "2026-11-05_easy.gpx")
    activity_id = storage.list_activities("plan")[0].id
    assert client.post(f"/api/activities/{activity_id}/review").status_code == 200
    assert len(fake.calls) == 2

    other = storage.load_plan("plan")
    other.meta.id, other.meta.status = "next", "draft"
    storage.save_plan(other)
    lifecycle.accept("next")
    assert client.post(f"/api/activities/{activity_id}/review").status_code == 409


def test_a_review_never_changes_the_plan(client, fake):
    before = storage.load_plan("plan")
    upload(client, "2026-11-05_easy.gpx")
    assert storage.load_plan("plan") == before


def test_review_schema_is_compatible():
    import anthropic

    from app.models import RunReview

    schema = anthropic.transform_schema(RunReview)
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])


def test_the_review_gets_the_recording_and_the_units_note(client, fake):
    upload(client, "2026-11-05_easy.gpx")
    prompt = fake.calls[0]["messages"][0]["content"]
    recording = prompt.split("The recording, one row every 30 seconds")[1].split("Athlete's reference paces")[0]
    assert '"columns": ["km", "seconds", "pace_s_per_km", "hr", "altitude_m", "cadence"]' in recording
    assert "[0.0, 0," in recording and "FIT profile" in prompt and '"extra"' in prompt
    assert "lat" not in recording
