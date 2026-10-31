"""The real provider code against fakes of the Garmin clients. No test reaches the network."""
import base64
import io
import json
import logging
import stat
import zipfile
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import create_autospec

import garminconnect
import pytest
from garminconnect import Garmin
from garminconnect.exceptions import GarminConnectAuthenticationError, GarminConnectConnectionError

from app import storage
from app.garmin import login, provider
from app.garmin.provider import GarminError, NotSupported, PersonalApiProvider, UnofficialProvider

FAKE_KEY = "fake-personal-key-zebra"
FAKE_REFRESH = "fake-refresh-token-zebra"
SINCE = datetime(2026, 10, 26, tzinfo=timezone.utc)
RAW = {
    "activityId": 123,
    "startTimeGMT": "2026-11-02 23:12:30",
    "startTimeLocal": "2026-11-03 07:12:30",
    "activityType": {"typeKey": "running"},
    "distance": 8000.0,
    "duration": 2690.5,
    "averageHR": 140.0,
    "maxHR": 160.0,
}


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


@pytest.fixture
def personal():
    provider.save_secret(provider.PERSONAL_KEY_FILE, FAKE_KEY)
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
    assert personal.status().connected and not personal.can_push_workouts()
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
    assert len(personal.session.gets) == 2 and len(personal.session.posts) == 1
    assert stat.S_IMODE((storage.DATA_DIR / "secrets" / provider.PERSONAL_BEARER_FILE).stat().st_mode) == 0o600


def test_personal_api_mints_a_new_token_once_after_a_401(personal):
    personal.session.fail_next_get = 401
    assert len(personal.list_activities(SINCE)) == 1
    assert len(personal.session.posts) == 2


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
    plain = PersonalApiProvider(FakeSession())
    assert not plain.status().connected
    with pytest.raises(GarminError, match="app.garmin.login"):
        plain.list_activities(SINCE)


def test_personal_api_cannot_push():
    with pytest.raises(NotSupported):
        PersonalApiProvider().push_workout(b"", {}, date(2026, 11, 3))


def test_an_odd_activity_shape_is_reported_without_values():
    with pytest.raises(GarminError, match="unexpected shape") as caught:
        provider.remote_activity({"activityId": 1, "secretish": "value"})
    assert "value" not in str(caught.value).replace("values", "")


def test_unofficial_resumes_tokens_and_reads(mocked):
    mocked.get_activities_by_date.return_value = [RAW]
    mocked.download_activity.return_value = zipped()
    plain = UnofficialProvider()
    assert plain.status().connected and plain.can_push_workouts()
    [activity] = plain.list_activities(SINCE)
    assert activity.id == "123"
    assert plain.download_fit("123") == b".FITDATA"
    mocked.login.assert_called_once_with(tokenstore=str(storage.DATA_DIR / "secrets"))
    mocked.get_activities_by_date.assert_called_once_with("2026-10-26", None, None, "asc")
    mocked.download_activity.assert_called_once_with("123", dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)


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
    with pytest.raises(GarminError, match="app.garmin.login") as caught:
        UnofficialProvider().list_activities(SINCE)
    assert FAKE_REFRESH not in str(caught.value)


def test_mode_selects_the_provider(monkeypatch):
    for mode, kind in (("off", provider.NullProvider), ("personal_api", PersonalApiProvider), ("unofficial", UnofficialProvider)):
        monkeypatch.setattr(provider, "settings", replace(provider.settings, garmin_mode=mode))
        assert type(provider.make_provider()) is kind
    monkeypatch.setenv("OPENSTRIDE_DEMO", "1")
    assert type(provider.make_provider()) is provider.NullProvider


def test_status_reads_state_without_calling_garmin():
    provider.write_json(provider.state_path(), {"last_sync_at": "t", "last_error": "e"})
    status = provider.NullProvider().status()
    assert (status.mode, status.connected, status.last_sync_at, status.last_error) == ("off", False, "t", "e")


def test_secrets_are_mode_600_in_a_private_folder():
    provider.save_secret("x", "1")
    provider.save_secret("x", "2")
    assert stat.S_IMODE((storage.DATA_DIR / "secrets" / "x").stat().st_mode) == 0o600
    assert stat.S_IMODE((storage.DATA_DIR / "secrets").stat().st_mode) == 0o700


def test_a_key_saved_loosely_is_tightened_before_use(personal):
    (storage.DATA_DIR / "secrets" / provider.PERSONAL_KEY_FILE).chmod(0o644)
    personal.list_activities(SINCE)
    assert stat.S_IMODE((storage.DATA_DIR / "secrets" / provider.PERSONAL_KEY_FILE).stat().st_mode) == 0o600


def test_login_saves_the_personal_key_only(monkeypatch, capsys):
    monkeypatch.setattr(login, "settings", replace(login.settings, garmin_mode="personal_api"))
    monkeypatch.setattr(login, "getpass", lambda prompt: FAKE_KEY)
    login.main()
    assert (storage.DATA_DIR / "secrets" / provider.PERSONAL_KEY_FILE).read_text() == FAKE_KEY
    assert FAKE_KEY not in capsys.readouterr().out


def test_unofficial_login_uses_the_password_once_and_never_stores_it(monkeypatch, capsys):
    cls = create_autospec(Garmin)
    cls.return_value.login.return_value = (None, None)
    monkeypatch.setattr(garminconnect, "Garmin", cls)
    monkeypatch.setattr(login, "settings", replace(login.settings, garmin_mode="unofficial"))
    monkeypatch.setattr(login, "getpass", lambda prompt: "hunter2-password")
    monkeypatch.setattr("builtins.input", lambda prompt: "me@example.com")
    login.main()
    args, kwargs = cls.call_args
    assert args == ("me@example.com", "hunter2-password") and callable(kwargs["prompt_mfa"])
    cls.return_value.login.assert_called_once_with(tokenstore=str(storage.DATA_DIR / "secrets"))
    assert "grey area" in capsys.readouterr().out
    assert not any("hunter2" in path.read_text() for path in storage.DATA_DIR.rglob("*") if path.is_file())


def test_login_with_mode_off_explains():
    with pytest.raises(SystemExit, match="GARMIN_MODE"):
        login.main()


def test_failed_logs_and_errors_hide_secrets(personal, caplog):
    caplog.set_level(logging.DEBUG)
    personal.session.fail_next_get = 500
    with pytest.raises(GarminError):
        personal.list_activities(SINCE)
    assert FAKE_KEY not in caplog.text and jwt(1) not in caplog.text
