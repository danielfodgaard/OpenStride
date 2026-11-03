"""B3: two devices on one data folder. Thread tests here use real threads and real file locks."""
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, adapt, chat, lifecycle, logbook, main, storage
from app.models import PlanFile
from test_generator import FakeClient, reply as fake_message

DEMO = Path("examples/demo/data")
SAMPLE = Path("examples/sample_plan.json").read_text()
ROUNDS = 5
BLANK_ENTRY = {"title": "", "distance_km": 0, "rpe": 0, "note": "", "details": []}


def all_at_once(*calls):
    """Runs every call at the same moment. Fails, rather than hangs, on a deadlock."""
    barrier = threading.Barrier(len(calls))

    def run(call):
        barrier.wait(timeout=10)
        return call()

    with ThreadPoolExecutor(len(calls)) as pool:
        return [future.result(timeout=30) for future in [pool.submit(run, call) for call in calls]]


def assert_files_valid(folder):
    """No half-written or leftover file anywhere."""
    assert not list(folder.rglob("*.tmp"))
    for path in folder.rglob("*.json"):
        json.loads(path.read_text())


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-25")
    shutil.copytree(DEMO, storage.DATA_DIR)
    return TestClient(main.app)


def pending_proposal():
    plan_id = lifecycle.active_plan().meta.id
    return plan_id, next(p for p in storage.list_proposals(plan_id) if p.status == "pending").proposal_id


def draft(plan_id="plan"):
    plan_file = PlanFile.model_validate_json(SAMPLE)
    plan_file.meta.id = plan_id
    storage.plan_dir(plan_id).mkdir(parents=True, exist_ok=True)
    storage.save_plan(plan_file)
    return plan_file


# Atomic writes

def test_failure_between_temp_write_and_replace_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "plan.json"
    storage.write_json(target, '{"version": 1}')

    def crash(*args):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        storage.write_json(target, '{"version": 2}')
    assert json.loads(target.read_text()) == {"version": 1}
    assert [path.name for path in tmp_path.iterdir()] == ["plan.json"]


def test_process_killed_mid_write_leaves_a_whole_file_and_no_listed_junk(tmp_path):
    """A real os._exit before the replace, so no cleanup code gets to run."""
    storage_dir = tmp_path / "data"
    code = f"""
import os
from pathlib import Path
from app import storage
target = Path({str(storage_dir)!r}) / "activities" / "run.json"
storage.write_json(target, '{{"version": 1}}')
os.replace = lambda *args: os._exit(9)
storage.write_json(target, '{{"version": 2}}')
"""
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": "."})
    assert result.returncode == 9
    folder = storage_dir / "activities"
    assert json.loads((folder / "run.json").read_text()) == {"version": 1}
    assert [path.name for path in folder.glob("*.json")] == ["run.json"]  # as listings see the folder
    assert len(list(folder.glob(".*.tmp"))) == 1


def test_fsync_happens_before_replace(tmp_path, monkeypatch):
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda *a: (order.append("replace"), real_replace(*a))[1])
    storage.write_json(tmp_path / "a.json", "{}")
    assert order == ["fsync", "replace"]


def test_concurrent_writers_of_one_path_do_not_collide(tmp_path):
    target = tmp_path / "metrics_cache.json"
    bodies = [json.dumps({"writer": i, "pad": "x" * 100_000}) for i in range(16)]
    all_at_once(*[lambda body=body: storage.write_json(target, body) for body in bodies])
    assert target.read_text() in bodies
    assert [path.name for path in tmp_path.iterdir()] == ["metrics_cache.json"]


def test_raw_files_are_written_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    storage.save_raw("run", "run.fit", b"old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk gone")))
    with pytest.raises(OSError):
        storage.save_raw("run", "run.fit", b"new")
    assert storage.find_raw("run").read_bytes() == b"old"


def test_written_files_keep_normal_permissions_and_secrets_can_ask_for_private(tmp_path):
    os.umask(previous := os.umask(0o022))
    storage.write_json(tmp_path / "a.json", "{}")
    storage.write_file(tmp_path / "token", b"x", mode=0o600)
    assert (tmp_path / "a.json").stat().st_mode & 0o777 == 0o666 & ~previous
    assert (tmp_path / "token").stat().st_mode & 0o777 == 0o600


# Lock behaviour

def test_locks_nest_in_one_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    draft()
    with storage.plan_lock("plan"), storage.plan_lock("plan"), storage.activities_lock(), storage.activities_lock():
        pass
    with storage.plan_lock("plan"):  # released: another thread gets it within the timeout
        pass

    def other_thread():
        with storage.plan_lock("plan"):
            return True

    assert all_at_once(other_thread) == [True]


def test_plan_lock_is_refused_while_holding_the_activities_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    draft()
    with storage.activities_lock():
        with pytest.raises(RuntimeError, match="before activities_lock"):
            storage.plan_lock("plan")
    with storage.plan_lock("plan"), storage.activities_lock(), storage.plan_lock("plan"):
        pass  # already held, so no new wait and no ordering problem


def test_the_plan_lock_excludes_other_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "LOCK_TIMEOUT_S", 0.05)
    draft()

    def other_thread():
        with storage.plan_lock("plan"):
            return "got it"

    with storage.plan_lock("plan"):
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(storage.FileLockTimeout):
                pool.submit(other_thread).result()
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(other_thread).result() == "got it"


def test_lock_on_a_missing_plan_is_not_found_and_makes_no_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    with pytest.raises(storage.NotFound):
        storage.plan_lock("nope")
    assert not (tmp_path / "plans" / "nope").exists()


def test_a_stuck_lock_gives_503_not_a_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "LOCK_TIMEOUT_S", 0.05)
    draft()
    client = TestClient(main.app)
    with storage.plan_lock("plan"):
        assert client.post("/api/plans/plan/accept").status_code == 503  # the test client runs in its own thread


# Simultaneous requests

def test_accept_against_reject_one_wins_one_conflicts(demo):
    plan_id, proposal_id = pending_proposal()
    for _ in range(ROUNDS):
        proposal_path = storage.plan_dir(plan_id) / "proposals" / f"{proposal_id}.json"
        plan_path = storage.plan_dir(plan_id) / "plan.json"
        saved = proposal_path.read_text(), plan_path.read_text()

        responses = all_at_once(
            lambda: demo.post(f"/api/proposals/{proposal_id}/accept"),
            lambda: demo.post(f"/api/proposals/{proposal_id}/reject"),
        )
        codes = sorted(response.status_code for response in responses)
        assert codes == [200, 409]
        accepted = responses[0].status_code == 200
        assert storage.load_proposal(proposal_id).status == ("accepted" if accepted else "rejected")
        assert storage.load_plan(plan_id).meta.version == (2 if accepted else 1)
        assert_files_valid(storage.DATA_DIR)

        proposal_path.write_text(saved[0])
        plan_path.write_text(saved[1])
        (storage.plan_dir(plan_id) / "versions" / "plan_v2.json").unlink(missing_ok=True)


def test_two_accepts_make_one_new_version(demo):
    plan_id, proposal_id = pending_proposal()
    responses = all_at_once(*[lambda: demo.post(f"/api/proposals/{proposal_id}/accept")] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409]
    plan_file = storage.load_plan(plan_id)
    assert plan_file.meta.version == 2 and len(plan_file.meta.history) == 1
    assert_files_valid(storage.DATA_DIR)


def test_two_drafts_accepted_together_leave_one_active(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    for name in "abcd":
        draft(name)
    lifecycle.accept("a")
    all_at_once(*[lambda name=name: lifecycle.accept(name) for name in "bcd"])
    statuses = [plan["status"] for plan in storage.list_plans()]
    assert sorted(statuses) == ["active", "archived", "archived", "archived"]


def test_manual_logs_from_many_devices_all_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    fields = {"date": date(2026, 1, 5), "modality": "strength", "duration_min": 30, **BLANK_ENTRY}
    made = all_at_once(*[lambda: logbook.create(None, fields)] * 12)
    assert len({entry.id for entry in made}) == 12 == len(storage.list_all_manual())


def test_chat_during_another_chat_keeps_both_conversations(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    draft()
    lifecycle.accept("plan")
    answer = {"reply": "Fine.", "action": "none", "change_request": "", "scope": "this_week", "flags": []}
    all_at_once(*[
        lambda text=text: chat.send("plan", text, FakeClient(fake_message(answer)), date(2026, 11, 11))
        for text in ("one", "two", "three", "four")
    ])
    thread = storage.load_chat("plan")
    assert sorted(m.text for m in thread if m.role == "user") == ["four", "one", "three", "two"]
    assert len(thread) == 8


def test_regenerate_during_another_regenerate_is_plan_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    draft()
    from app import generator

    def regenerated_elsewhere_during_the_call(old, feedback, request, client, today):
        again = old.model_copy(deep=True)
        changed = again.model_copy(deep=True)
        changed.meta.feedback_history = ["from the phone"]
        storage.save_plan(changed)
        return again

    monkeypatch.setattr(generator, "regenerate_plan", regenerated_elsewhere_during_the_call)
    with pytest.raises(lifecycle.PlanChanged):
        lifecycle.regenerate("plan", "slower")
    assert storage.load_plan("plan").meta.feedback_history == ["from the phone"]
    assert not list((storage.plan_dir("plan") / "versions").glob("draft_v*.json"))


def test_plan_and_activities_work_together_never_deadlocks(demo):
    """accept takes plan then activities; imports and logging compete for both."""
    plan_id, proposal_id = pending_proposal()
    fit = Path("examples/activities/cross/2026-11-12_pool_swim.fit")
    fields = {"date": date(2026, 11, 24), "modality": "strength", "duration_min": 30, **BLANK_ENTRY}
    workout = next(a for a in storage.list_activities(plan_id))
    results = all_at_once(
        lambda: demo.post(f"/api/proposals/{proposal_id}/accept"),
        lambda: activities.import_files([(fit.name, fit.read_bytes())], "", 0),
        lambda: logbook.create(None, fields),
        lambda: activities.edit(workout.id, "note from the phone", None, None),
        lambda: adapt.restore(plan_id, 1),
        lambda: logbook.create(plan_id, fields),
    )
    assert results[0].status_code in (200, 409)
    assert storage.load_activity(workout.id).user_note == "note from the phone"
    assert_files_valid(storage.DATA_DIR)


# If-Match

@pytest.fixture
def plan_client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    draft()
    return TestClient(main.app)


def test_get_plan_has_an_etag_that_changes_with_the_file(plan_client):
    first = plan_client.get("/api/plans/plan").headers["etag"]
    assert first == plan_client.get("/api/plans/plan").headers["etag"]
    storage.save_plan(storage.load_plan("plan"))
    assert first != plan_client.get("/api/plans/plan").headers["etag"]
    assert first.startswith('"1-')


def test_stale_if_match_is_409_plan_changed_and_changes_nothing(plan_client):
    stale = plan_client.get("/api/plans/plan").headers["etag"]
    storage.save_plan(storage.load_plan("plan"))
    response = plan_client.post("/api/plans/plan/accept", headers={"If-Match": stale})
    assert response.status_code == 409 and response.json() == {"error": "plan_changed"}
    assert storage.load_plan("plan").meta.status == "draft"


def test_current_if_match_and_no_header_both_pass(plan_client):
    etag = plan_client.get("/api/plans/plan").headers["etag"]
    assert plan_client.post("/api/plans/plan/accept", headers={"If-Match": etag}).status_code == 200
    assert plan_client.post("/api/plans/plan/chat/clear").status_code == 204
    assert plan_client.post("/api/plans/plan/chat/clear", headers={"If-Match": "*"}).status_code == 204


@pytest.mark.parametrize("method, path", [
    ("post", "/api/plans/plan/accept"),
    ("post", "/api/plans/plan/regenerate"),
    ("delete", "/api/plans/plan"),
    ("post", "/api/plans/plan/restore/1"),
    ("post", "/api/plans/plan/chat"),
    ("post", "/api/plans/plan/chat/clear"),
    ("post", "/api/plans/plan/manual"),
])
def test_every_mutating_plan_route_checks_if_match(plan_client, method, path):
    bodies = {
        "regenerate": {"feedback": "x"},
        "chat": {"text": "x"},
        "manual": {"date": "2026-01-05", "modality": "strength", "duration_min": 30},
    }
    kwargs = {"json": bodies[path.rsplit("/", 1)[1]]} if path.rsplit("/", 1)[1] in bodies else {}
    response = plan_client.request(method, path, headers={"If-Match": '"stale"'}, **kwargs)
    assert response.status_code == 409 and response.json() == {"error": "plan_changed"}


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_proposal_routes_check_if_match_on_their_plan(demo, action):
    plan_id, proposal_id = pending_proposal()
    response = demo.post(f"/api/proposals/{proposal_id}/{action}", headers={"If-Match": '"stale"'})
    assert response.status_code == 409 and response.json() == {"error": "plan_changed"}
    assert storage.load_proposal(proposal_id).status == "pending"
    etag = demo.get(f"/api/plans/{plan_id}").headers["etag"]
    assert demo.post(f"/api/proposals/{proposal_id}/{action}", headers={"If-Match": etag}).status_code == 200


def test_if_match_on_a_missing_plan_is_404(plan_client):
    assert plan_client.post("/api/plans/nope/accept", headers={"If-Match": '"x"'}).status_code == 404


def test_new_plan_ids_never_repeat_under_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    ids = all_at_once(*[lambda: storage.new_plan_id("same-second")] * 12)
    assert len(set(ids)) == 12
