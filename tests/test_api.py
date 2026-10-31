import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, storage

SAMPLE = Path("examples/sample_plan.json").read_text()
PLAN_ID = json.loads(SAMPLE)["meta"]["id"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PLANS_DIR", tmp_path)
    (tmp_path / f"{PLAN_ID}.json").write_text(SAMPLE)
    (tmp_path.parent / "secret.json").write_text("{}")
    return TestClient(main.app)


def test_index_and_renderer_are_served(client):
    assert "<form" in client.get("/").text
    assert "renderPlan" in client.get("/render.js").text


def test_list_and_get_plan(client):
    plans = client.get("/api/plans").json()
    assert [p["id"] for p in plans] == [PLAN_ID]
    assert client.get(f"/api/plans/{PLAN_ID}").json()["meta"]["id"] == PLAN_ID


def test_missing_plan_is_404(client):
    assert client.get("/api/plans/nope").status_code == 404


def test_plan_id_cannot_escape_plans_dir(client):
    assert storage.load_plan("../secret") is None
    assert client.get("/api/plans/..%2Fsecret").status_code == 404


def test_invalid_request_is_422_with_message(client):
    response = client.post("/api/plan", json={"goal_description": "x"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"]


def test_startup_fails_clearly_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        with TestClient(main.app):
            pass
