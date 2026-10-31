import io
import json
import logging
import stat
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import fitdecode
import pytest
from fastapi.testclient import TestClient

from app import activities, generator, lifecycle, main, storage, workout_fit
from app.garmin import delivery, provider, router as garmin_router
from app.garmin.provider import GarminError
from app.models import PlanFile
from test_review import reply as review_reply
from conftest import save_secret

FAKE_TOKEN = "fake-garmin-token-zebra"
WEEK = 3


class FakePusher:
    def __init__(self):
        self.pushes = []
        self.deleted = []
        self.fail_on = set()
        self.fail_delete = False

    def can_push_workouts(self):
        return True

    def push_workout(self, workout_json, day):
        if day in self.fail_on:
            raise GarminError("Garmin request failed (Timeout).")
        self.pushes.append((workout_json, day))
        return f"w{len(self.pushes)}"

    def delete_workout(self, workout_id):
        if self.fail_delete:
            raise GarminError("Garmin request failed (Timeout).")
        self.deleted.append(workout_id)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-10-31")
    monkeypatch.setattr(generator, "make_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **k: review_reply())))


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def plan():
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "plan"
    storage.save_plan(plan_file)
    return lifecycle.accept("plan")


@pytest.fixture
def pusher(monkeypatch):
    fake = FakePusher()
    monkeypatch.setattr(garmin_router, "make_pusher", lambda: fake)
    monkeypatch.setattr(garmin_router, "push_mode", lambda: "unofficial")
    return fake


def set_mode(monkeypatch, sync="personal_api", push="off"):
    monkeypatch.setattr(provider, "settings", replace(provider.settings, garmin_sync=sync, garmin_push=push))


def runs(plan_file, number=WEEK):
    return [i for i, s in enumerate(plan_file.plan.weeks[number - 1].sessions) if s.modality == "run"]


def other(plan_file, number=WEEK):
    return next(i for i, s in enumerate(plan_file.plan.weeks[number - 1].sessions) if s.modality != "run")


def edit_pace(plan_file, index, number=WEEK):
    session = plan_file.plan.weeks[number - 1].sessions[index]
    if session.steps:
        session.steps[0].target_pace = "9:00/km"
    session.target_pace = "9:00/km"
    storage.save_plan(plan_file)


def test_status_in_mode_off_works(client):
    body = client.get("/api/garmin/status").json()
    assert (body["sync"], body["push"], body["connected"], body["can_push_workouts"]) == ("off", "off", False, False)
    assert not body["push_connected"] and body["push_blocked"] is None and body["last_sync_at"] is None


def test_sync_in_mode_off_is_501(client):
    response = client.post("/api/garmin/sync")
    assert response.status_code == 501 and response.json() == {"error": "garmin_not_configured"}


def test_status_for_a_connected_mode(client, monkeypatch):
    set_mode(monkeypatch, push="unofficial")
    monkeypatch.setenv(provider.PERSONAL_KEY_ENV, FAKE_TOKEN)
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, "{}")
    body = client.get("/api/garmin/status").json()
    assert body["push"] == "unofficial" and body["connected"] and body["can_push_workouts"]
    assert FAKE_TOKEN not in json.dumps(body)


def test_personal_mode_status_has_no_push(client, monkeypatch):
    set_mode(monkeypatch)
    body = client.get("/api/garmin/status").json()
    assert body["sync"] == "personal_api" and not body["connected"] and not body["can_push_workouts"]


def test_sync_in_a_connected_mode_returns_the_spec_shape(client, monkeypatch):
    set_mode(monkeypatch)  # no key set: the error comes back in the result
    body = client.post("/api/garmin/sync").json()
    assert body["imported"] == [] and body["skipped"] == body["matched"] == body["unplanned"] == 0
    assert provider.PERSONAL_KEY_ENV in body["warnings"][0]
    assert provider.PERSONAL_KEY_ENV in client.get("/api/garmin/status").json()["last_error"]


def test_download_works_in_mode_off(client, plan):
    index = runs(plan)[0]
    session = plan.plan.weeks[WEEK - 1].sessions[index]
    response = client.get(f"/api/plans/plan/sessions/{WEEK}/{index}/workout.fit")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.ant.fit"
    assert response.headers["content-disposition"].endswith(f'{workout_fit.file_name(session)}"')
    with fitdecode.FitReader(io.BytesIO(response.content), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        assert any(isinstance(frame, fitdecode.FitDataMessage) and frame.name == "workout_step" for frame in reader)


def test_download_works_on_a_draft_too(client):
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "draft"
    storage.save_plan(plan_file)
    assert client.get(f"/api/plans/draft/sessions/1/{runs(plan_file, 1)[0]}/workout.fit").status_code == 200


def test_download_refuses_strength_and_unknown_sessions(client, plan):
    assert client.get(f"/api/plans/plan/sessions/{WEEK}/{other(plan)}/workout.fit").status_code == 422
    assert client.get(f"/api/plans/plan/sessions/{WEEK}/99/workout.fit").status_code == 404
    assert client.get("/api/plans/plan/sessions/99/0/workout.fit").status_code == 404
    assert client.get("/api/plans/none/sessions/1/0/workout.fit").status_code == 404


def test_download_with_an_odd_pace_says_so(client, plan):
    index = runs(plan)[0]
    session = plan.plan.weeks[WEEK - 1].sessions[index]
    session.steps, session.target_pace = [], "comfortably hard – ish"
    storage.save_plan(plan)
    response = client.get(f"/api/plans/plan/sessions/{WEEK}/{index}/workout.fit")
    assert response.status_code == 200 and "comfortably hard" in response.headers["x-openstride-notes"]


def test_send_session_needs_garmin(client, plan):
    url = f"/api/plans/plan/sessions/{WEEK}/{runs(plan)[0]}/send-to-garmin"
    assert client.post(url).json() == {"error": "garmin_not_configured"}
    assert client.post(url).status_code == 501


def test_personal_mode_cannot_send(client, plan, monkeypatch):
    set_mode(monkeypatch, "personal_api")
    for url in (f"/api/plans/plan/sessions/{WEEK}/{runs(plan)[0]}/send-to-garmin", f"/api/plans/plan/weeks/{WEEK}/send-to-garmin"):
        response = client.post(url)
        assert response.status_code == 501 and response.json() == {"error": "push_not_supported"}


def test_send_session_schedules_on_the_session_date(client, plan, pusher):
    index = runs(plan)[0]
    session = plan.plan.weeks[WEEK - 1].sessions[index]
    body = client.post(f"/api/plans/plan/sessions/{WEEK}/{index}/send-to-garmin").json()
    assert body == {"workout_id": "w1", "scheduled_date": session.date.isoformat(), "notes": []}
    json_body, day = pusher.pushes[0]
    assert day == session.date and json_body["workoutName"] == workout_fit.workout_name(session)
    saved = delivery.load_sent("plan")[f"{WEEK}/{index}"]
    assert saved["workout_id"] == "w1" and saved["hash"] == workout_fit.fingerprint(session)


def test_resending_replaces_the_old_workout(client, plan, pusher):
    url = f"/api/plans/plan/sessions/{WEEK}/{runs(plan)[0]}/send-to-garmin"
    client.post(url)
    assert client.post(url).json()["workout_id"] == "w2"
    assert pusher.deleted == ["w1"]
    assert delivery.load_sent("plan")[f"{WEEK}/{runs(plan)[0]}"]["workout_id"] == "w2"


def test_a_failed_delete_is_reported_and_the_new_id_kept(client, plan, pusher):
    url = f"/api/plans/plan/sessions/{WEEK}/{runs(plan)[0]}/send-to-garmin"
    client.post(url)
    pusher.fail_delete = True
    assert "w1 is still there" in client.post(url).json()["notes"][0]
    assert delivery.load_sent("plan")[f"{WEEK}/{runs(plan)[0]}"]["workout_id"] == "w2"


def test_a_failed_push_is_a_502_and_keeps_the_mapping(client, plan, pusher):
    index = runs(plan)[0]
    url = f"/api/plans/plan/sessions/{WEEK}/{index}/send-to-garmin"
    client.post(url)
    pusher.fail_on.add(plan.plan.weeks[WEEK - 1].sessions[index].date)
    response = client.post(url)
    assert response.status_code == 502 and response.json()["error"] == "garmin_error"
    assert delivery.load_sent("plan")[f"{WEEK}/{index}"]["workout_id"] == "w1" and pusher.deleted == []


def test_send_refuses_strength_and_non_active_plans(client, plan, pusher):
    assert client.post(f"/api/plans/plan/sessions/{WEEK}/{other(plan)}/send-to-garmin").status_code == 422
    assert client.post(f"/api/plans/plan/sessions/{WEEK}/99/send-to-garmin").status_code == 404
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = "draft"
    storage.save_plan(plan_file)
    assert client.post("/api/plans/draft/sessions/1/0/send-to-garmin").status_code == 409
    assert pusher.pushes == []


def test_week_sends_only_runs_and_reports_the_shape(client, plan, pusher):
    body = client.post(f"/api/plans/plan/weeks/{WEEK}/send-to-garmin").json()
    assert body == {"sent": len(runs(plan)), "failed": [], "notes": []}
    assert len(pusher.pushes) == len(runs(plan))


def test_a_week_with_a_failing_session_finishes_the_rest(client, plan, pusher):
    index = runs(plan)[1]
    session = plan.plan.weeks[WEEK - 1].sessions[index]
    pusher.fail_on.add(session.date)
    body = client.post(f"/api/plans/plan/weeks/{WEEK}/send-to-garmin").json()
    assert body["sent"] == len(runs(plan)) - 1
    assert body["failed"] == [{"session": session.title, "error": "Garmin request failed (Timeout)."}]


def test_week_skips_past_and_done_sessions(client, plan, pusher, monkeypatch):
    week_one = plan.plan.weeks[0]
    tuesday = next(s for s in week_one.sessions if s.modality == "run" and s.date == date(2026, 11, 3))
    monkeypatch.setenv("OPENSTRIDE_TODAY", tuesday.date.isoformat())
    activities.import_files([("r.fit", Path("examples/activities/2026-11-03_tue_easy_easy-run.fit").read_bytes())], "", 0)
    ahead = [s for s in week_one.sessions if s.modality == "run" and s.date > tuesday.date]
    assert client.post("/api/plans/plan/weeks/1/send-to-garmin").json()["sent"] == len(ahead)
    assert all(day > tuesday.date for _, day in pusher.pushes)


def test_week_status_counts_sent_and_changed(client, plan, pusher):
    url = f"/api/plans/plan/weeks/{WEEK}/garmin"
    assert client.get(url).json() == {"sent": 0, "changed": 0}
    client.post(f"/api/plans/plan/weeks/{WEEK}/send-to-garmin")
    assert client.get(url).json() == {"sent": len(runs(plan)), "changed": 0}
    edit_pace(plan, runs(plan)[0])
    assert client.get(url).json() == {"sent": len(runs(plan)), "changed": 1}


def test_week_status_works_in_mode_off(client, plan):
    assert client.get(f"/api/plans/plan/weeks/{WEEK}/garmin").json() == {"sent": 0, "changed": 0}
    assert client.get("/api/plans/plan/weeks/99/garmin").status_code == 404


def test_changed_only_resends_just_the_changed_session(client, plan, pusher):
    url = f"/api/plans/plan/weeks/{WEEK}/send-to-garmin"
    client.post(url)
    count = len(pusher.pushes)
    edit_pace(plan, runs(plan)[0])
    assert client.post(f"{url}?changed_only=true").json()["sent"] == 1
    assert len(pusher.pushes) == count + 1 and pusher.deleted == ["w1"]
    assert client.get(f"/api/plans/plan/weeks/{WEEK}/garmin").json()["changed"] == 0
    assert client.post(f"{url}?changed_only=true").json()["sent"] == 0


def test_changed_only_ignores_sessions_never_sent(client, plan, pusher):
    assert client.post(f"/api/plans/plan/weeks/{WEEK}/send-to-garmin?changed_only=true").json()["sent"] == 0


def test_a_session_removed_from_the_plan_is_removed_from_garmin(client, plan, pusher):
    url = f"/api/plans/plan/weeks/{WEEK}/send-to-garmin"
    client.post(url)
    index = runs(plan)[-1]
    del plan.plan.weeks[WEEK - 1].sessions[index]
    storage.save_plan(plan)
    assert client.get(f"/api/plans/plan/weeks/{WEEK}/garmin").json()["changed"] >= 1
    client.post(f"{url}?changed_only=true")
    assert f"{WEEK}/{index}" not in delivery.load_sent("plan")
    assert client.get(f"/api/plans/plan/weeks/{WEEK}/garmin").json()["changed"] == 0


def test_a_past_session_counts_as_sent_but_not_changed(client, plan, pusher, monkeypatch):
    client.post(f"/api/plans/plan/weeks/{WEEK}/send-to-garmin")
    edit_pace(plan, runs(plan)[0])
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2027-01-01")
    assert client.get(f"/api/plans/plan/weeks/{WEEK}/garmin").json() == {"sent": len(runs(plan)), "changed": 0}


def test_week_send_with_mode_off(client, plan):
    assert client.post(f"/api/plans/plan/weeks/{WEEK}/send-to-garmin").status_code == 501


def test_the_real_unofficial_push_path_through_the_router(client, plan, monkeypatch):
    from unittest.mock import create_autospec

    import garminconnect
    from garminconnect import Garmin

    set_mode(monkeypatch, push="unofficial")
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, json.dumps({"di_refresh_token": FAKE_TOKEN}))
    cls = create_autospec(Garmin)
    cls.return_value.login.return_value = (None, None)
    cls.return_value.upload_workout.return_value = {"workoutId": 41}
    monkeypatch.setattr(garminconnect, "Garmin", cls)
    index = runs(plan)[0]
    session = plan.plan.weeks[WEEK - 1].sessions[index]
    body = client.post(f"/api/plans/plan/sessions/{WEEK}/{index}/send-to-garmin").json()
    assert body["workout_id"] == "41"
    cls.return_value.upload_workout.assert_called_once_with(workout_fit.garmin_json(session))
    cls.return_value.schedule_workout.assert_called_once_with(41, session.date.isoformat())
    cls.return_value.push_workout_to_device.assert_called_once_with(41)


def test_tokens_stay_in_secrets_and_out_of_everything_else(client, plan, monkeypatch, caplog):
    from unittest.mock import create_autospec

    import garminconnect
    from garminconnect import Garmin

    caplog.set_level(logging.DEBUG)
    set_mode(monkeypatch, push="unofficial")
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, json.dumps({"di_refresh_token": FAKE_TOKEN}))
    cls = create_autospec(Garmin)
    cls.return_value.login.return_value = (None, None)
    cls.return_value.upload_workout.side_effect = RuntimeError(f"401 for {FAKE_TOKEN}")
    monkeypatch.setattr(garminconnect, "Garmin", cls)
    response = client.post(f"/api/plans/plan/sessions/{WEEK}/{runs(plan)[0]}/send-to-garmin")
    assert response.status_code == 502 and FAKE_TOKEN not in response.text
    assert FAKE_TOKEN not in caplog.text
    secret = storage.DATA_DIR / "secrets" / provider.UNOFFICIAL_TOKEN_FILE
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    outside = [p for p in storage.DATA_DIR.rglob("*") if p.is_file() and "secrets" not in p.parts]
    assert outside and not any(FAKE_TOKEN.encode() in path.read_bytes() for path in outside)


def test_review_prompts_never_read_the_secrets_folder():
    source = "".join(path.read_text() for path in Path("app/prompts").glob("*.py"))
    source += Path("app/review.py").read_text() + Path("app/chat.py").read_text()
    assert "secrets" not in source and "garmin" not in source.lower()
