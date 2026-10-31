"""The real provider code against fakes of the Garmin clients. No test reaches the network."""
import base64
import io
import json
import logging
import stat
import zipfile
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import create_autospec

import garminconnect
import pytest
from garminconnect import Garmin
from garminconnect.exceptions import GarminConnectAuthenticationError, GarminConnectConnectionError

from app import storage
from app.garmin import provider
from app.garmin.provider import GarminError, NotSupported, PersonalApiProvider, UnofficialProvider

FAKE_KEY = "fake-personal-key-zebra"
FAKE_REFRESH = "fake-refresh-token-zebra"
SINCE = datetime(2026, 10, 26, tzinfo=timezone.utc)
FIXTURE = json.loads(Path("tests/fixtures/garmin_activities.json").read_text())  # a real reply's shape, sanitised
RAW = FIXTURE["running"]


def jwt(secret="payload"):
    body = base64.urlsafe_b64encode(json.dumps({"exp": 4102444800, "x": secret}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def zipped(name="123_ACTIVITY.fit", content=b".FITDATA"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


class Response:
    def __init__(self, status=200, text="", json_data=None, content=b""):
        self.status_code, self.text, self._json, self.content = status, text, json_data, content
        self.ok = status < 400

    def json(self):
        return self._json


class FakeSession:
    """Stands in for requests.Session under Garmin's official client."""

    def __init__(self, pages=None, download=None):
        self.pages = pages if pages is not None else [[RAW]]
        self.download = download
        self.posts = []
        self.gets = []
        self.fail_next_get = None

    def post(self, url, headers, timeout):
        self.posts.append((url, headers))
        return Response(text=jwt(len(self.posts)))

    def get(self, url, headers, params, timeout):
        self.gets.append((url, headers, params))
        if self.fail_next_get:
            status, self.fail_next_get = self.fail_next_get, None
            return Response(status=status, text=f"echoing {FAKE_KEY}")
        if "download-service" in url:
            return Response(content=self.download)
        return Response(json_data=self.pages.pop(0) if self.pages else [], content=b"[]")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(provider, "PAGE_DELAY_S", 0)


@pytest.fixture
def personal(monkeypatch):
    monkeypatch.setenv(provider.PERSONAL_KEY_ENV, FAKE_KEY)
    return PersonalApiProvider(FakeSession())


@pytest.fixture
def mocked(monkeypatch):
    """A Garmin class with the installed library's real signatures: a wrong call raises TypeError."""
    provider.save_secret(provider.UNOFFICIAL_TOKEN_FILE, json.dumps({"di_refresh_token": FAKE_REFRESH}))
    cls = create_autospec(Garmin)
    instance = cls.return_value
    instance.ActivityDownloadFormat = Garmin.ActivityDownloadFormat
    instance.login.return_value = (None, None)
    instance.upload_workout.return_value = {"workoutId": 777}
    monkeypatch.setattr(garminconnect, "Garmin", cls)
    return instance


def test_personal_api_exchanges_the_key_and_lists_activities(personal):
    assert not personal.can_push_workouts()
    [activity] = personal.list_activities(SINCE)
    assert activity.id == "123" and activity.sport == "running" and activity.distance_m == 8000
    assert activity.start_time == datetime(2026, 11, 2, 23, 12, 30, tzinfo=timezone.utc)
    assert activity.local_start == datetime(2026, 11, 3, 7, 12, 30)
    url, headers = personal.session.posts[0]
    assert url == "https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token"
    assert headers["Authorization"] == f"ApiKey {FAKE_KEY}"
    url, headers, params = personal.session.gets[0]
    assert url.endswith("/activitylist-service/activities/search/activities")
    assert headers["Authorization"] == f"Bearer {jwt(1)}" and params["startDate"] == "2026-10-26"


def test_personal_api_reuses_its_token_and_pages_and_filters(personal):
    full_page = [RAW | {"activityId": n} for n in range(provider.PAGE_SIZE)]
    old = RAW | {"activityId": 999, "startTimeGMT": "2026-10-20 10:00:00"}
    personal.session.pages = [full_page, {"activities": [RAW, old]}]
    assert len(personal.list_activities(SINCE)) == provider.PAGE_SIZE + 1
    assert len(personal.session.gets) == 3 and len(personal.session.posts) == 1  # to the empty page
    assert stat.S_IMODE((storage.DATA_DIR / "secrets" / provider.PERSONAL_BEARER_FILE).stat().st_mode) == 0o600


def test_personal_api_mints_a_new_token_once_after_a_401(personal):
    personal.session.fail_next_get = 401
    assert len(personal.list_activities(SINCE)) == 1
    assert len(personal.session.posts) == 2


def test_personal_api_backs_off_on_429_then_carries_on(personal, monkeypatch):
    waits = []
    monkeypatch.setattr(provider.time, "sleep", waits.append)
    personal.session.fail_next_get = 429
    assert len(personal.list_activities(SINCE)) == 1
    assert waits[0] == provider.BACKOFF_S[0]


def test_personal_api_gives_up_after_the_last_backoff(personal, monkeypatch):
    monkeypatch.setattr(provider.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(personal.session, "get", lambda *a, **k: Response(status=429, text="slow down"))
    with pytest.raises(GarminError, match="rate limiting"):
        personal.list_activities(SINCE)


def test_the_real_reply_shape_gives_the_facts_sync_needs():
    run = provider.remote_activity(FIXTURE["running"])
    assert run.id == "123" and run.sport == "running" and run.distance_m == 8000 and run.duration_s == 2880
    assert run.start_time == datetime(2026, 11, 2, 23, 12, 30, tzinfo=timezone.utc)
    assert run.local_start == datetime(2026, 11, 3, 7, 12, 30)
    assert (run.avg_hr, run.max_hr) == (152, 170)
    assert provider.remote_activity(FIXTURE["strength"]).sport == "strength_training"


def test_personal_api_download_unzips_the_fit(personal):
    personal.session.download = zipped()
    assert personal.download_fit("123") == b".FITDATA"
    assert personal.session.gets[-1][0].endswith("/download-service/files/activity/123")


@pytest.mark.parametrize("content,expected", [
    (zipped("track.gpx", b"<gpx/>"), b""),
    (zipped("A.FIT", b"x"), b"x"),
    (b".FITraw", b".FITraw"),
    (b"PK broken", b""),
])
def test_downloads_of_every_shape(content, expected):
    assert provider.fit_from_download(content) == expected


def test_personal_api_errors_carry_no_response_text(personal):
    personal.session.fail_next_get = 500
    with pytest.raises(GarminError) as caught:
        personal.list_activities(SINCE)
    assert "HttpError HTTP 500" in str(caught.value) and FAKE_KEY not in str(caught.value)
    assert caught.value.__cause__ is None


def test_personal_api_without_a_key_says_how_to_connect():
    with pytest.raises(GarminError, match=provider.PERSONAL_KEY_ENV):
        PersonalApiProvider(FakeSession()).list_activities(SINCE)


def test_personal_api_cannot_push():
    with pytest.raises(NotSupported):
        PersonalApiProvider().push_workout(b"", {}, date(2026, 11, 3))


def test_an_odd_activity_shape_is_reported_without_values():
    with pytest.raises(GarminError, match="unexpected shape") as caught:
        provider.remote_activity({"activityId": 1, "secretish": "value"})
    assert "value" not in str(caught.value).replace("values", "")


def test_unofficial_resumes_saved_tokens_without_a_password(mocked):
    UnofficialProvider().push_workout(b"", {}, date(2026, 11, 3))
    mocked.login.assert_called_once_with(tokenstore=str(storage.DATA_DIR / "secrets" / "garmin-unofficial"))


def test_unofficial_push_uploads_schedules_and_sends_to_the_watch(mocked):
    workout_id = UnofficialProvider().push_workout(b"", {"workoutName": "Easy"}, date(2026, 11, 3))
    assert workout_id == "777"
    mocked.upload_workout.assert_called_once_with({"workoutName": "Easy"})
    mocked.schedule_workout.assert_called_once_with(777, "2026-11-03")
    mocked.push_workout_to_device.assert_called_once_with(777)


def test_a_failed_device_push_keeps_the_scheduled_workout(mocked):
    mocked.push_workout_to_device.side_effect = GarminConnectConnectionError("no device")
    assert UnofficialProvider().push_workout(b"", {}, date(2026, 11, 3)) == "777"
    mocked.delete_workout.assert_not_called()


def test_a_failed_schedule_removes_the_upload(mocked):
    mocked.schedule_workout.side_effect = GarminConnectConnectionError("calendar down")
    with pytest.raises(GarminError):
        UnofficialProvider().push_workout(b"", {}, date(2026, 11, 3))
    mocked.delete_workout.assert_called_once_with(777)


def test_rejected_tokens_ask_for_a_new_login(mocked):
    mocked.login.side_effect = GarminConnectAuthenticationError(f"bad {FAKE_REFRESH}")
    with pytest.raises(GarminError, match="Garmin settings") as caught:
        UnofficialProvider().push_workout(b"", {}, date(2026, 11, 3))
    assert FAKE_REFRESH not in str(caught.value)


def test_secrets_are_mode_600_in_a_private_folder():
    provider.save_secret("x", "1")
    provider.save_secret("x", "2")
    assert stat.S_IMODE((storage.DATA_DIR / "secrets" / "x").stat().st_mode) == 0o600
    assert stat.S_IMODE((storage.DATA_DIR / "secrets").stat().st_mode) == 0o700


def test_failed_logs_and_errors_hide_secrets(personal, caplog):
    caplog.set_level(logging.DEBUG)
    personal.session.fail_next_get = 500
    with pytest.raises(GarminError):
        personal.list_activities(SINCE)
    assert FAKE_KEY not in caplog.text and jwt(1) not in caplog.text


def set_modes(monkeypatch, sync, push):
    monkeypatch.setattr(provider, "settings", replace(provider.settings, garmin_sync=sync, garmin_push=push))


def test_each_capability_has_its_own_provider(monkeypatch):
    set_modes(monkeypatch, "personal_api", "unofficial")
    assert type(provider.make_provider()) is PersonalApiProvider and type(provider.make_pusher()) is UnofficialProvider
    set_modes(monkeypatch, "off", "off")
    assert type(provider.make_provider()) is provider.NullProvider and type(provider.make_pusher()) is provider.NullProvider
    set_modes(monkeypatch, "personal_api", "unofficial")
    monkeypatch.setenv("OPENSTRIDE_DEMO", "1")
    assert type(provider.make_provider()) is provider.NullProvider and type(provider.make_pusher()) is provider.NullProvider


def test_the_unofficial_client_can_never_read_activities():
    with pytest.raises(GarminError):
        UnofficialProvider().list_activities(SINCE)
    with pytest.raises(GarminError):
        UnofficialProvider().download_fit("1")


def test_status_reads_state_without_calling_garmin():
    provider.write_json(provider.state_path(), {"last_sync_at": "t", "last_error": "e"})
    status = provider.status()
    assert (status.sync, status.push, status.connected, status.last_sync_at, status.last_error) == ("off", "off", False, "t", "e")


def test_status_of_each_capability(monkeypatch):
    set_modes(monkeypatch, "personal_api", "unofficial")
    assert not provider.status().connected and not provider.status().can_push_workouts
    monkeypatch.setenv(provider.PERSONAL_KEY_ENV, FAKE_KEY)
    provider.save_secret(provider.UNOFFICIAL_TOKEN_FILE, "{}")
    assert provider.status().connected and provider.status().can_push_workouts


def test_the_old_mode_still_sets_both_capabilities(monkeypatch):
    from app import config
    monkeypatch.delenv("GARMIN_SYNC"); monkeypatch.delenv("GARMIN_PUSH")
    expected = {"off": ("off", "off"), "personal_api": ("personal_api", "off"), "unofficial": ("personal_api", "unofficial")}
    for mode, pair in expected.items():
        monkeypatch.setenv("GARMIN_MODE", mode)
        assert (config.load().garmin_sync, config.load().garmin_push) == pair
        assert config.load().garmin_mode_used
    monkeypatch.setenv("GARMIN_PUSH", "off")
    assert config.load().garmin_push == "off"
    monkeypatch.setenv("GARMIN_SYNC", "off")
    assert not config.load().garmin_mode_used and config.load().garmin_sync == "off"  # the new settings win
