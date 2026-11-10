"""The web server: the stream, the approval gate as the athlete uses it, and what it refuses to serve."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import config, mirror, plan, workspace
from app.hub import hub


@pytest.fixture
def client(demo_ws, monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_DEMO", "1")  # the scripted agent that answers from the cards
    from app import server

    with TestClient(server.app) as made:
        yield made


def settle(client) -> list[dict]:
    for _ in range(200):
        data = client.get("/api/stream", params={"limit": 200}).json()
        if not data["busy"]:
            return data["events"]
        time.sleep(0.02)
    raise AssertionError("the agent never finished")


def say(client, text: str) -> list[dict]:
    assert client.post("/api/messages", data={"text": text}).status_code == 200
    return settle(client)


def test_a_message_gets_a_card_and_a_reply(client):
    events = say(client, "How is this week going?")
    kinds = [(e["type"], e.get("role") or e.get("card") or e.get("state")) for e in events]
    assert ("message", "athlete") in kinds and ("card", "week") in kinds and kinds[-1] == ("turn", "ended")
    assert [e for e in events if e["type"] == "message"][-1]["text"].startswith("Week 4 of 10")
    assert client.get("/api/stream", params={"after": events[-1]["id"]}).json()["events"] == []
    assert client.post("/api/messages", data={"text": "  "}).status_code == 422


def test_the_athlete_approves_and_the_plan_changes_and_is_sent(client):
    events = say(client, "move my next easy run")
    proposal_id = next(e for e in events if e["type"] == "proposal")["proposal_id"]
    before = plan.load().plan.model_dump()
    card = client.get("/api/card", params={"type": "proposal", "args": json.dumps({"id": proposal_id})}).json()
    assert card["status"] == "pending" and card["diff"][0]["entries"][0]["kind"] == "moved"
    assert client.get("/api/glance").json()["pending_proposals"] == [proposal_id]
    response = client.post(f"/api/proposals/{proposal_id}/approve")
    assert response.status_code == 200 and response.json()["calendar"]["sent"]
    assert plan.load().plan.model_dump() != before
    assert mirror.calendar() and workspace.changed(["plan", "proposals"]) == []
    assert client.post(f"/api/proposals/{proposal_id}/approve").status_code == 409
    assert "approved proposal" in json.loads((workspace.state() / "notices.json").read_text())[0]
    assert any(e["type"] == "notice" and e["text"].startswith("Plan updated.") for e in settle(client))


def test_a_rejection_with_a_reason_goes_back_to_the_coach(client):
    events = say(client, "move my next easy run")
    proposal_id = next(e for e in events if e["type"] == "proposal")["proposal_id"]
    before = plan.load().plan.model_dump()
    assert client.post(f"/api/proposals/{proposal_id}/reject", json={"note": "Friday is no good"}).json() == {"status": "rejected"}
    events = settle(client)
    assert plan.load().plan.model_dump() == before
    assert any(e["type"] == "message" and e["role"] == "athlete" and "Friday is no good" in e["text"] for e in events)
    assert f"(I rejected proposal {proposal_id}.) Friday is no good" in hub.runtime.requests[-1].prompt


def test_cards_and_their_errors(client):
    assert client.get("/api/card", params={"type": "week", "args": json.dumps({"week": 2})}).json()["week_number"] == 2
    assert client.get("/api/card", params={"type": "week", "args": json.dumps({"week": 99})}).status_code == 404
    assert client.get("/api/card", params={"type": "table"}).status_code == 422
    assert client.get("/api/card", params={"type": "week", "args": "not json"}).status_code == 422


def test_the_drawer_reads_files_but_never_secrets_or_git(client):
    listing = client.get("/api/files").json()["files"]
    tiers = {f["path"]: f["tier"] for f in listing}
    assert tiers["plan/plan.json"] == "gated" and tiers["athlete/memory.md"] == "authored" and tiers["activities/index.jsonl"] == "recorded"
    assert not any(path.startswith((".git/", "scratch/")) for path in tiers)
    opened = client.get("/api/file", params={"path": "plan/request.json"}).json()
    assert json.loads(opened["text"])["runs_per_week"] == 4 and opened["log"]
    for path in ("../state/secrets/approval_key", "/etc/passwd", ".git/config", "../../.env", "plan/../../state/sync.json"):
        assert client.get("/api/file", params={"path": path}).status_code == 404
        assert client.get("/api/raw", params={"path": path}).status_code == 404
    assert client.get("/api/raw", params={"path": "plan/plan.json"}).status_code == 415
    assert client.get("/api/log").json()["log"]


def test_an_attached_file_is_kept_for_the_agent_and_a_recording_needs_a_provider_that_takes_it(client):
    response = client.post("/api/messages", data={"text": "My race bib"}, files=[("files", ("bib.png", b"\x89PNG....", "image/png")), ("files", ("run.fit", b"FIT", "application/octet-stream"))])
    assert response.status_code == 200
    settle(client)
    prompt = hub.runtime.requests[-1].prompt
    saved = next(workspace.root().glob("scratch/uploads/*_bib.png"))
    assert f"saved at {saved.relative_to(workspace.root()).as_posix()}" in prompt
    assert "run.fit" in prompt and "can't take files" in prompt


def test_status_sync_and_backup(client):
    status = client.get("/api/status").json()
    assert status["provider"] == status["provider"] | {"name": "demo", "connected": True} and status["runtime"] == "scripted"
    assert client.post("/api/sync").json()["new"] == []
    backup = client.get("/api/backup")
    assert backup.status_code == 200 and backup.content[:2] == b"PK"
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(backup.content)).namelist()
    assert "workspace/plan/plan.json" in names and not any("secrets" in name for name in names)


def test_with_a_password_nothing_is_served_without_logging_in(demo_ws, monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_PASSWORD", "correct horse")
    monkeypatch.setenv("OPENSTRIDE_DEMO", "1")
    config.reload()
    from app import server

    monkeypatch.setattr(server, "WRONG_PASSWORD_DELAY_S", 0)
    with TestClient(server.app, follow_redirects=False) as client:
        assert client.get("/api/health").status_code == 200
        for path in ("/api/stream", "/api/glance", "/api/files", "/api/backup"):
            assert client.get(path).status_code == 401
        assert client.post("/api/proposals/x/approve").status_code == 401
        assert client.post("/api/messages", data={"text": "hi"}).status_code == 401
        assert client.get("/", headers={"accept": "text/html"}).status_code == 303
        assert client.post("/login", data={"password": "wrong"}).headers["location"] == "/login?failed=1"
        assert client.post("/login", data={"password": "correct horse"}).headers["location"] == "/"
        assert client.get("/api/glance").status_code == 200


def test_the_server_refuses_to_listen_openly_without_a_password(monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_HOST", "0.0.0.0")
    config.reload()
    assert "Refusing to start" in config.open_server_problem()
    monkeypatch.setenv("OPENSTRIDE_PASSWORD", "x")
    config.reload()
    assert config.open_server_problem() is None
