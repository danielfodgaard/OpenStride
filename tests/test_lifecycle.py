import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import lifecycle, main, storage
from app.models import PlanFile
from test_generator import REQUEST, SAMPLE, TODAY, FakeClient, reply

SAMPLE_TEXT = Path("examples/sample_plan.json").read_text()


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    return tmp_path


def draft(plan_id):
    plan_file = PlanFile.model_validate_json(SAMPLE_TEXT)
    plan_file.meta.id = plan_id
    plan_file.schema_version = "2.0"
    storage.save_plan(plan_file)
    return plan_id


@pytest.fixture
def client():
    return TestClient(main.app)


def test_accept_activates_and_writes_version_1():
    result = lifecycle.accept(draft("one"))
    assert result.meta.status == "active" and result.meta.version == 1
    assert result.meta.accepted_at
    assert (storage.plan_dir("one") / "versions" / "plan_v1.json").exists()
    assert lifecycle.active_plan().meta.id == "one"


def test_accept_archives_the_previous_active_plan():
    lifecycle.accept(draft("one"))
    lifecycle.accept(draft("two"))
    assert storage.load_plan("one").meta.status == "archived"
    assert lifecycle.active_plan().meta.id == "two"
    assert [p["status"] for p in storage.list_plans()].count("active") == 1


def test_only_drafts_can_be_accepted(client):
    lifecycle.accept(draft("one"))
    assert client.post("/api/plans/one/accept").status_code == 409


def test_only_drafts_can_be_deleted(client):
    lifecycle.accept(draft("one"))
    lifecycle.accept(draft("two"))
    assert client.delete("/api/plans/one").status_code == 409  # archived
    assert client.delete("/api/plans/two").status_code == 409  # active
    assert storage.load_plan("one") and storage.load_plan("two")


def test_discard_removes_the_draft(client):
    draft("one")
    assert client.delete("/api/plans/one").status_code == 204
    assert not storage.plan_dir("one").exists()
    assert client.delete("/api/plans/one").status_code == 404


def test_regenerate_keeps_the_previous_draft_and_records_feedback():
    draft("one")
    fake = FakeClient(reply(SAMPLE["plan"]), reply(SAMPLE["plan"]))
    lifecycle.regenerate("one", "less volume", None, fake, TODAY)
    result = lifecycle.regenerate("one", "long run on Sunday", None, fake, TODAY)

    assert result.meta.id == "one"
    assert result.meta.feedback_history == ["less volume", "long run on Sunday"]
    assert sorted(p.name for p in (storage.plan_dir("one") / "versions").iterdir()) == ["draft_v1.json", "draft_v2.json"]
    assert "Current draft plan:" in fake.prompts[1]
    assert "- less volume" in fake.prompts[1] and "long run on Sunday" in fake.prompts[1]


def test_regenerate_uses_an_edited_request():
    draft("one")
    edited = REQUEST.model_copy(update={"notes": "now on trails"})
    fake = FakeClient(reply(SAMPLE["plan"]))
    result = lifecycle.regenerate("one", "trails please", edited, fake, TODAY)
    assert result.request.notes == "now on trails" and "now on trails" in fake.prompts[0]


def test_regenerate_failure_leaves_the_draft_alone():
    draft("one")
    with pytest.raises(lifecycle.generator.GenerationError):
        lifecycle.regenerate("one", "x", None, FakeClient(reply(SAMPLE["plan"], "refusal")), TODAY)
    assert storage.load_plan("one").meta.feedback_history == []
    assert not (storage.plan_dir("one") / "versions").exists()


def test_regenerate_with_a_passed_race_date_is_a_clear_error():
    draft("one")
    with pytest.raises(lifecycle.generator.GenerationError, match="race date has passed"):
        lifecycle.regenerate("one", "x", None, FakeClient(), TODAY.replace(year=2028))


def test_only_drafts_can_be_regenerated(client):
    lifecycle.accept(draft("one"))
    assert client.post("/api/plans/one/regenerate", json={"feedback": "x"}).status_code == 409


def test_import_makes_a_fresh_draft_whatever_the_file_claims(client):
    data = json.loads(SAMPLE_TEXT)
    data["meta"] |= {"id": "../../evil", "status": "active", "version": 9}
    response = client.post("/api/plans/import", files={"file": ("plan.json", json.dumps(data))})
    assert response.status_code == 200
    meta = response.json()["meta"]
    assert meta["status"] == "draft" and meta["version"] == 1
    assert meta["id"].endswith("_import")
    assert storage.load_plan(meta["id"])


def test_import_rejects_junk(client):
    response = client.post("/api/plans/import", files={"file": ("plan.json", "not json")})
    assert response.status_code == 422 and "Not a valid plan file" in response.json()["detail"]


def test_active_route(client):
    assert client.get("/api/plans/active").status_code == 404
    lifecycle.accept(draft("one"))
    assert client.get("/api/plans/active").json()["meta"]["id"] == "one"


def test_list_shows_status_and_version(client):
    lifecycle.accept(draft("one"))
    listed = client.get("/api/plans").json()[0]
    assert listed["status"] == "active" and listed["version"] == 1 and listed["race_date"] == "2027-01-10"
