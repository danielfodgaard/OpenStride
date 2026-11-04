import json
import logging
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import activities, generator, lifecycle, main, metrics, review, storage
from app.garmin import provider, sync
from app.garmin.provider import GarminError, RemoteActivity
from app.models import PlanFile
from test_review import reply as review_reply
from conftest import save_secret

SAMPLES = Path("examples/activities")
TOP_LEVEL = [path.name for path in sorted(SAMPLES.glob("*.fit"))]
FAKE_TOKEN = "fake-garmin-token-zebra"


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return review_reply()


class FakeProvider:
    """Serves real files, as a Garmin account would."""

    def __init__(self, names=TOP_LEVEL, extra=()):
        self.files = {}
        self.items = list(extra)
        for number, name in enumerate(names, 1):
            content = (SAMPLES / name).read_bytes()
            summary = activities.parse_file(name, content, "")
            self.files[str(number)] = content
            self.items.append(RemoteActivity(
                id=str(number),
                start_time=datetime.fromisoformat(summary.start_utc),
                sport=summary.sport,
                distance_m=summary.distance_km * 1000,
                duration_s=summary.moving_time_s,
            ))
        self.since = []
        self.downloads = []

    def list_activities(self, since):
        self.since.append(since)
        return [item for item in self.items if item.start_time >= since]

    def download_fit(self, remote_id):
        self.downloads.append(remote_id)
        return self.files[remote_id]


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-25")
    messages = FakeMessages()
    monkeypatch.setattr(generator, "make_client", lambda: SimpleNamespace(messages=messages))
    return messages


def add_active(plan_id="plan"):
    plan_file = PlanFile.model_validate_json(Path("examples/sample_plan_v3.json").read_text())
    plan_file.meta.id = plan_id
    storage.save_plan(plan_file)
    return lifecycle.accept(plan_id)


def by_external_id():
    return {a.external_id: a for a in storage.list_all_activities()}


def strength(remote_id="900", day="2026-11-04 18:00:00"):
    return RemoteActivity(
        id=remote_id,
        start_time=datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
        sport="strength_training",
        distance_m=0,
        duration_s=2700,
        local_start=datetime.fromisoformat(day),
    )


def test_first_sync_imports_all_and_the_second_none(setup):
    add_active()
    fake = FakeProvider()
    first = sync.sync(fake)
    assert len(first["imported"]) == len(TOP_LEVEL)
    assert first["skipped"] == 0 and first["warnings"] == []
    assert first["matched"] + first["unplanned"] == len(TOP_LEVEL)
    assert len(setup.calls) == len(TOP_LEVEL)

    fake.downloads.clear()
    second = sync.sync(fake)
    assert second["imported"] == [] and second["skipped"] == len(TOP_LEVEL)
    assert fake.downloads == [] and len(setup.calls) == len(TOP_LEVEL)


def test_activities_match_their_sessions():
    plan_file = add_active()
    result = sync.sync(FakeProvider(["2026-11-03_tue_easy_easy-run.fit", "2026-11-05_thu_easy_easy-run.fit"]))
    assert result["matched"] == 2 and result["unplanned"] == 0
    for activity in storage.list_all_activities():
        session = plan_file.plan.weeks[activity.match.week_number - 1].sessions[activity.match.session_index]
        assert session.date == activity.date and session.modality == "run"
    assert all(storage.has_series(a.id) and storage.find_raw(a.id) for a in storage.list_all_activities())


def test_the_utc_evening_run_lands_on_its_local_day():
    plan_file = add_active()
    fake = FakeProvider(["2026-11-03_tue_easy_easy-run.fit"])
    assert fake.items[0].start_time == datetime(2026, 11, 2, 23, 12, 30, tzinfo=timezone.utc)
    sync.sync(fake)
    [activity] = storage.list_all_activities()
    assert activity.date == date(2026, 11, 3) and activity.date.strftime("%a") == "Tue"
    assert activity.start_utc.startswith("2026-11-02T23:12:30")
    assert activity.match.planned_date == "2026-11-03"
    assert plan_file.plan.weeks[0].sessions[activity.match.session_index].day == "Tue"


def test_a_cross_sport_goes_through_cross_matching():
    add_active()
    result = sync.sync(FakeProvider(["cross/2026-11-04_ride.fit"]))
    [activity] = storage.list_all_activities()
    assert activity.modality == "cross" and activity.sport == "cycling"
    assert len(result["imported"]) == 1


def test_a_run_already_uploaded_by_hand_is_skipped():
    add_active()
    activities.import_files([(TOP_LEVEL[0], (SAMPLES / TOP_LEVEL[0]).read_bytes())], "", 0)
    result = sync.sync(FakeProvider(TOP_LEVEL[:1]))
    assert result["imported"] == [] and result["skipped"] == 1
    assert len(storage.list_all_activities()) == 1


def test_the_same_garmin_activity_is_found_across_plans():
    add_active("plan")
    activities.import_fit_bytes((SAMPLES / TOP_LEVEL[0]).read_bytes(), "garmin", "garmin:7")
    result = activities.import_fit_bytes(b"not read", "garmin", "garmin:7")
    assert result["status"] == "duplicate"


def test_a_workout_deleted_here_does_not_come_back():
    add_active()
    fake = FakeProvider(TOP_LEVEL[:1])
    [activity_id] = sync.sync(fake)["imported"]
    activities.delete(activity_id)
    assert sync.sync(fake)["imported"] == []


def test_no_api_key_still_imports_and_warns(monkeypatch, tmp_path):
    add_active()
    data = tmp_path
    monkeypatch.undo()  # the real make_client, which refuses with no key
    monkeypatch.setattr(storage, "DATA_DIR", data)
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-25")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = sync.sync(FakeProvider(TOP_LEVEL[:2]))
    assert len(result["imported"]) == 2
    assert sum("review failed" in warning for warning in result["warnings"]) == 2
    assert provider.load_state()["last_error"] is None


def test_any_review_failure_keeps_the_import(monkeypatch):
    add_active()
    monkeypatch.setattr(review, "review_activity", lambda activity: 1 / 0)
    result = sync.sync(FakeProvider(TOP_LEVEL[:1]))
    assert len(result["imported"]) == 1 and "review failed" in result["warnings"][0]


def test_future_dated_runs_keep_their_warning(monkeypatch):
    add_active()
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-10-31")
    result = sync.sync(FakeProvider(TOP_LEVEL[:1]))
    assert activities.FUTURE_WARNING in result["warnings"]


def test_sync_with_no_active_plan(setup):
    result = sync.sync(FakeProvider(TOP_LEVEL[:1]))
    assert result["imported"] and result["unplanned"] == 1
    [activity] = storage.list_all_activities()
    assert activity.plan_id == "" and activity.match.session_index == -1 and activity.external_id == "garmin:1"


def test_since_with_no_plan_and_no_history_looks_back_a_month():
    since = sync.since_for({}, None)
    assert timedelta(days=29) < datetime.now(timezone.utc) - since < timedelta(days=31)


def test_since_starts_a_week_before_the_plan():
    plan_file = add_active()
    assert sync.since_for({}, plan_file) == datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert sync.since_for({"last_sync_at": "2026-01-01T00:00:00+00:00"}, plan_file) == datetime(2026, 10, 26, tzinfo=timezone.utc)


def test_since_overlaps_the_last_sync_for_late_uploads():
    plan_file = add_active()
    assert sync.since_for({"last_sync_at": "2026-11-20T12:00:00+00:00"}, plan_file) == datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


def test_a_run_uploaded_after_the_last_sync_is_still_found():
    add_active()
    fake = FakeProvider(TOP_LEVEL[:1])
    storage.write_json(provider.state_path(), {"last_sync_at": fake.items[0].start_time.isoformat()})
    assert len(sync.sync(fake)["imported"]) == 1


def test_a_listing_error_is_recorded_in_status_without_raising():
    class Broken(FakeProvider):
        def list_activities(self, since):
            raise GarminError("Garmin refused the login or key.")

    result = sync.sync(Broken())
    assert result["imported"] == [] and "refused" in result["warnings"][0]
    status = provider.status()
    assert "refused" in status["last_error"] and status["last_sync_at"] is None


def test_an_unexpected_provider_error_is_recorded_without_its_text():
    class Broken(FakeProvider):
        def list_activities(self, since):
            raise RuntimeError(f"token {FAKE_TOKEN}")

    sync.sync(Broken())
    state = provider.state_path().read_text()
    assert "RuntimeError" in state and FAKE_TOKEN not in state


def test_a_download_error_leaves_the_activity_for_next_time():
    add_active()

    class Flaky(FakeProvider):
        fail = True

        def download_fit(self, remote_id):
            if remote_id == "2" and self.fail:
                raise GarminError("Garmin request failed (Timeout).")
            return super().download_fit(remote_id)

    fake = Flaky(TOP_LEVEL[:3])
    first = sync.sync(fake)
    assert len(first["imported"]) == 2
    assert any("activity 2 was not imported" in warning for warning in first["warnings"])
    state = provider.load_state()
    assert "1 of 3" in state["last_error"] and "last_sync_at" not in state

    fake.fail = False
    second = sync.sync(fake)
    assert len(second["imported"]) == 1 and second["skipped"] == 2
    assert provider.load_state()["last_error"] is None


def test_strength_becomes_a_logged_session_with_no_file():
    add_active()
    fake = FakeProvider([], [strength()])
    result = sync.sync(fake)
    assert fake.downloads == [] and result["matched"] + result["unplanned"] == 1
    [entry] = storage.list_all_manual()
    assert entry.modality == "strength" and entry.duration_min == 45 and entry.date == date(2026, 11, 4)
    assert entry.plan_id == "plan" and entry.distance_km == 0

    second = sync.sync(fake)
    assert second["imported"] == [] and second["skipped"] == 1
    assert len(storage.list_all_manual()) == 1


def test_strength_with_no_plan_and_no_route_opens_like_any_workout():
    sync.sync(FakeProvider([], [strength()]))
    [entry] = storage.list_all_manual()
    client = TestClient(main.app)
    page = client.get(f"/api/activities/{entry.id}").json()
    assert page["manual"] and page["has_series"] is False and page["has_raw"] is False
    assert page["match_label"] == "Unplanned" and page["title"] == "Strength"
    assert client.get(f"/api/activities/{entry.id}/series").status_code == 404
    assert client.get("/api/activities").status_code == 200


def test_future_dated_strength_waits_for_its_day(monkeypatch):
    fake = FakeProvider([], [strength(day="2026-11-26 18:00:00")])
    result = sync.sync(fake)
    assert result["imported"] == [] and "future date" in result["warnings"][0]
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-26")
    assert len(sync.sync(fake)["imported"]) == 1


def summary_only(remote_id="55"):
    return RemoteActivity(
        id=remote_id,
        start_time=datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc),
        sport="trail_running",
        distance_m=8000,
        duration_s=2700,
        local_start=datetime(2026, 11, 3, 7, 0),
        avg_hr=140,
        max_hr=160,
    )


def test_an_unusable_file_falls_back_to_a_partial_summary(setup):
    add_active()
    fake = FakeProvider([], [summary_only()])
    fake.files["55"] = b""
    result = sync.sync(fake)
    assert result["matched"] == 1 and any("only the summary" in w for w in result["warnings"])
    [activity] = storage.list_all_activities()
    assert activity.partial and activity.external_id == "garmin:55"
    assert activity.date == date(2026, 11, 3) and activity.modality == "run" and activity.avg_hr == 140
    assert activity.avg_pace == "5:38/km" and activity.laps == [] and activity.km_splits == []
    assert len(setup.calls) == 1
    page = TestClient(main.app).get(f"/api/activities/{activity.id}").json()
    assert page["partial"] and page["has_series"] is False and page["has_raw"] is False
    assert metrics.refresh(lifecycle.active_plan()).weeks[0].actual_km == 8


def test_a_partial_summary_is_not_imported_twice():
    add_active()
    fake = FakeProvider([], [summary_only()])
    fake.files["55"] = b""
    sync.sync(fake)
    assert sync.sync(fake)["skipped"] == 1 and len(storage.list_all_activities()) == 1


def test_a_file_with_only_totals_is_marked_partial(monkeypatch):
    add_active()
    session = {"sport": "running", "total_distance": 8000.0, "total_timer_time": 2700.0, "start_time": datetime(2026, 11, 3, 7, tzinfo=timezone.utc)}
    monkeypatch.setattr(activities, "read_fit_frames", lambda content: ([], [], [session], {}, {}, []))
    fake = FakeProvider([], [summary_only()])
    fake.files["55"] = b"totals"
    sync.sync(fake)
    [activity] = storage.list_all_activities()
    assert activity.partial and not storage.has_series(activity.id) and activity.distance_km == 8


def test_a_good_file_is_not_partial():
    add_active()
    sync.sync(FakeProvider(TOP_LEVEL[:1]))
    assert not storage.list_all_activities()[0].partial


def test_two_syncs_at_once_import_once():
    add_active()
    fake = FakeProvider(TOP_LEVEL[:2])
    threads = [threading.Thread(target=sync.sync, args=(fake,)) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert len(storage.list_all_activities()) == 2


def test_old_activity_files_still_load():
    activity = activities.parse_file(TOP_LEVEL[0], (SAMPLES / TOP_LEVEL[0]).read_bytes(), "")
    old = json.loads(activity.model_dump_json())
    del old["external_id"], old["partial"]
    (storage.DATA_DIR / "activities").mkdir()
    (storage.DATA_DIR / "activities" / "old.json").write_text(json.dumps(old))
    assert storage.list_all_activities()[0].external_id == ""


def test_background_sync_enforces_the_minimum(monkeypatch):
    monkeypatch.setattr(provider, "settings", replace(provider.settings, garmin_sync="personal_api"))
    monkeypatch.setattr(sync, "settings", replace(sync.settings, garmin_sync_minutes=1))
    waits = []
    monkeypatch.setattr(sync.stop_event, "wait", lambda seconds: waits.append(seconds) or True)
    sync.start()
    sync.stop()
    monkeypatch.undo()
    assert waits == [15 * 60]


@pytest.mark.parametrize("mode,minutes,demo", [("off", 30, ""), ("personal_api", 0, ""), ("personal_api", 30, "1")])
def test_background_sync_stays_off(monkeypatch, mode, minutes, demo):
    monkeypatch.setattr(provider, "settings", replace(provider.settings, garmin_sync=mode))
    monkeypatch.setattr(sync, "settings", replace(sync.settings, garmin_sync_minutes=minutes))
    monkeypatch.setenv("OPENSTRIDE_DEMO", demo)
    sync.start()
    assert sync.worker is None


def test_background_errors_never_escape(monkeypatch):
    def boom():
        raise RuntimeError(f"token {FAKE_TOKEN}")

    monkeypatch.setattr(sync, "sync", boom)
    waits = iter([False, True])
    monkeypatch.setattr(sync.stop_event, "wait", lambda seconds: next(waits))
    sync.loop(1)


def test_the_lifespan_starts_and_stops_background_sync(monkeypatch):
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used")
    monkeypatch.setattr(sync, "start", lambda: calls.append("start"))
    monkeypatch.setattr(sync, "stop", lambda: calls.append("stop"))
    with TestClient(main.app):
        assert calls == ["start"]
    assert calls == ["start", "stop"]


def test_the_demo_never_starts_background_sync(monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_DEMO", "1")
    monkeypatch.setattr(sync, "start", lambda: pytest.fail("started in demo mode"))
    with TestClient(main.app):
        pass


def test_a_secret_never_reaches_stored_files_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    add_active()
    save_secret(provider.PERSONAL_BEARER_FILE, FAKE_TOKEN)

    class Leaky(FakeProvider):
        def download_fit(self, remote_id):
            raise RuntimeError(f"Bearer {FAKE_TOKEN}")

    sync.sync(Leaky(TOP_LEVEL[:1]))
    sync.sync(FakeProvider(TOP_LEVEL[:1]))
    stored = [p for p in storage.DATA_DIR.rglob("*") if p.is_file() and "secrets" not in p.parts]
    assert stored and not any(FAKE_TOKEN.encode() in path.read_bytes() for path in stored)
    assert FAKE_TOKEN not in caplog.text


def test_a_second_sync_at_the_same_time_does_nothing():
    assert sync.sync_lock.acquire(blocking=False)
    try:
        result = sync.sync(FakeProvider(TOP_LEVEL[:1]))
    finally:
        sync.sync_lock.release()
    assert result["imported"] == [] and "already running" in result["warnings"][0]


def set_settings(monkeypatch, sync_mode="personal_api", push="off", minutes=0, old=False):
    new = replace(provider.settings, garmin_sync=sync_mode, garmin_push=push, garmin_sync_minutes=minutes, garmin_mode_used=old)
    monkeypatch.setattr(provider, "settings", new)
    monkeypatch.setattr(sync, "settings", new)


def test_the_setup_line_gives_each_state_without_the_key(monkeypatch):
    set_settings(monkeypatch, push="unofficial", minutes=30)
    monkeypatch.setenv(provider.PERSONAL_KEY_ENV, FAKE_TOKEN)
    monkeypatch.setattr(provider.PersonalApiProvider, "call", lambda self, request: None)
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, "{}")
    line = sync.setup_line()
    assert line == f"Garmin: sync=personal_api (key set, {len(FAKE_TOKEN)} chars), push=unofficial (logged in), auto-sync every 30 min"
    assert FAKE_TOKEN not in line


def test_the_setup_line_says_so_when_things_are_missing(monkeypatch):
    set_settings(monkeypatch, push="unofficial")
    line = sync.setup_line()
    assert "KEY MISSING" in line and "push=unofficial (not logged in)" in line and "auto-sync off" in line
    set_settings(monkeypatch, sync_mode="off")
    assert sync.setup_line() == "Garmin: sync=off, push=off, auto-sync off"


def test_the_setup_line_reports_a_failed_token_request(monkeypatch):
    set_settings(monkeypatch)
    monkeypatch.setenv(provider.PERSONAL_KEY_ENV, FAKE_TOKEN)

    def refuse(self, request):
        raise GarminError("Garmin refused the login or key.")

    monkeypatch.setattr(provider.PersonalApiProvider, "call", refuse)
    line = sync.setup_line()
    assert "TOKEN REQUEST FAILED: Garmin refused" in line and FAKE_TOKEN not in line


def test_the_old_setting_logs_one_warning_naming_the_new_ones(monkeypatch, caplog):
    set_settings(monkeypatch, old=True)
    caplog.set_level(logging.INFO, logger="app")
    sync.log_setup()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "GARMIN_SYNC" in warnings[0] and "GARMIN_PUSH" in warnings[0]


def test_no_warning_with_the_new_settings(monkeypatch, caplog):
    set_settings(monkeypatch)
    caplog.set_level(logging.INFO, logger="app")
    sync.log_setup()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]

