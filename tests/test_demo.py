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
    assert plan["meta"]["version"] == 1

    runs = demo.get("/api/activities", params={"plan_id": plan_id}).json()
    assert len(runs) == 5 and all(run["review"] for run in runs)
    assert any("calf" in run["user_note"] for run in runs)

    proposals = demo.get(f"/api/plans/{plan_id}/proposals").json()
    assert [p["result"]["recommendation"] for p in proposals] == ["change", "no_change"]

    accepted = demo.post(f"/api/proposals/{proposals[0]['proposal_id']}/accept")
    assert accepted.status_code == 200 and accepted.json()["meta"]["version"] == 2
    restored = demo.post(f"/api/plans/{plan_id}/restore/1").json()
    assert restored["meta"]["version"] == 3 and restored["plan"] == plan["plan"]

    assert demo.get(f"/api/plans/{plan_id}/progress").json()["latest_summary"]


def test_llm_actions_fail_clearly_and_the_run_is_kept(demo):
    plan_id = demo.get("/api/plans/active").json()["meta"]["id"]
    response = demo.post(f"/api/plans/{plan_id}/proposals", json={"scope": "this_week"})
    assert response.status_code == 502 and "API key" in response.json()["detail"]

    with open("examples/activities/2026-11-06_shakeout_unplanned.gpx", "rb") as file:
        result = demo.post("/api/activities", files=[("files", ("run.gpx", file.read()))]).json()[0]
    assert result["status"] == "imported" and "review failed" in result["message"]


def test_demo_does_not_touch_the_committed_data(demo):
    assert "examples" not in str(storage.DATA_DIR)
    plan_id = demo.get("/api/plans/active").json()["meta"]["id"]
    assert (storage.plan_dir(plan_id) / "plan.json").exists()
