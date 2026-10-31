import pytest
from fastapi.testclient import TestClient

from app import main, storage


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_DEMO", "1")
    monkeypatch.setenv("OPENSTRIDE_TODAY", "unused")  # restored after the test; the app overwrites it
    monkeypatch.setattr(storage, "DATA_DIR", storage.DATA_DIR)
    with TestClient(main.app) as client:
        yield client


def test_full_offline_flow_without_a_key(demo):
    plan = demo.get("/api/plans/active").json()
    plan_id = plan["meta"]["id"]
    assert plan["meta"]["version"] == 1 and plan["schema_version"] == "3.0"

    done = demo.get("/api/activities", params={"plan_id": plan_id}).json()
    files = [item for item in done if not item.get("manual")]
    logged = [item for item in done if item.get("manual")]
    assert len(files) == 13 and all(item["review"] for item in files)
    assert len(logged) == 2 and all(item["modality"] == "strength" for item in logged)
    assert sum(item["modality"] == "cross" for item in files) == 1
    assert any(item["user_note"] for item in files)

    thread = demo.get(f"/api/plans/{plan_id}/chat").json()
    assert [m["action"] for m in thread if m["role"] == "coach"] == ["none", "proposal_created"]
    proposal_id = thread[-1]["proposal_id"]

    accepted = demo.post(f"/api/proposals/{proposal_id}/accept")
    assert accepted.status_code == 200 and accepted.json()["meta"]["version"] == 2
    restored = demo.post(f"/api/plans/{plan_id}/restore/1").json()
    assert restored["meta"]["version"] == 3 and restored["plan"] == plan["plan"]

    assert demo.get(f"/api/plans/{plan_id}/progress").json()["latest_summary"]


def test_charts_work_offline_and_the_cache_is_there(demo):
    plan_id = demo.get("/api/plans/active").json()["meta"]["id"]
    assert (storage.plan_dir(plan_id) / "metrics_cache.json").exists()
    metrics = demo.get(f"/api/plans/{plan_id}/metrics").json()
    assert metrics["as_of"] == main.DEMO_TODAY
    assert metrics["days"][-1]["date"] == main.DEMO_TODAY
    assert metrics["current"]["ctl"] == metrics["days"][-1]["ctl"]
    assert metrics["easy_runs"] and metrics["quality_runs"]
    assert metrics["weeks"][2]["completed_strength"] == 2
    assert metrics["weeks"][1]["actual_cross_min"] > 0


def test_llm_actions_fail_clearly_and_the_run_is_kept(demo):
    plan_id = demo.get("/api/plans/active").json()["meta"]["id"]
    response = demo.post(f"/api/plans/{plan_id}/proposals", json={"scope": "this_week"})
    assert response.status_code == 502 and "API key" in response.json()["detail"]
    assert demo.post(f"/api/plans/{plan_id}/chat", json={"text": "hi"}).status_code == 502

    with open("examples/activities/cross/2026-11-12_pool_swim.fit", "rb") as file:
        result = demo.post("/api/activities", files=[("files", ("swim.fit", file.read()))]).json()[0]
    assert result["status"] == "imported" and "review failed" in result["message"]


def test_demo_does_not_touch_the_committed_data(demo):
    assert "examples" not in str(storage.DATA_DIR)
    plan_id = demo.get("/api/plans/active").json()["meta"]["id"]
    assert (storage.plan_dir(plan_id) / "plan.json").exists()
