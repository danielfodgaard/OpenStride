"""Talking to Garmin, behind one interface. Nothing outside this package sees a secret.

Exceptions from Garmin's clients can carry response text, so they never travel: `guarded`
turns them into a GarminError that holds only a class name and a status code.
"""
import io
import json
import logging
import os
import zipfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from app import storage
from app.config import settings

log = logging.getLogger(__name__)

PERSONAL_KEY_FILE = "garmin_personal_api_key"
PERSONAL_BEARER_FILE = "garmin_personal_bearer.json"
UNOFFICIAL_TOKEN_FILE = "garmin_tokens.json"  # python-garminconnect's name in a token directory
LOGIN_HINT = "Run: python -m app.garmin.login"
PAGE_SIZE = 50
MAX_PAGES = 40
MAX_FIT_BYTES = 64 * 1024 * 1024

# Fields of one activity in Garmin's activity list.
# Seen in Garmin's own client (garmin_personal_api/interactive.py): id and both start times.
FIELD_ID = "activityId"
FIELD_START_UTC = "startTimeGMT"
FIELD_START_LOCAL = "startTimeLocal"
# GUESSED for the Personal API: Garmin's client doesn't read these and the Swagger page could
# not be read. They are the names the consumer API uses (garminconnect/typed.py), and a third
# party's probe reports the same response class on the personal host. Check on a real key.
FIELD_TYPE = "activityType"
FIELD_TYPE_KEY = "typeKey"
FIELD_DISTANCE_M = "distance"
FIELD_DURATION_S = "duration"
FIELD_AVG_HR = "averageHR"
FIELD_MAX_HR = "maxHR"


class GarminError(Exception):
    """Its text is safe to store and show."""


class NotSupported(GarminError):
    pass


@dataclass
class GarminStatus:
    mode: str
    connected: bool
    last_sync_at: str | None
    last_error: str | None
    can_push_workouts: bool


@dataclass
class RemoteActivity:
    id: str
    start_time: datetime  # aware, UTC
    sport: str
    distance_m: float
    duration_s: float
    local_start: datetime | None = None
    avg_hr: int = 0
    max_hr: int = 0


class GarminProvider(Protocol):
    def status(self) -> GarminStatus: ...
    def list_activities(self, since: datetime) -> list[RemoteActivity]: ...
    def download_fit(self, remote_id: str) -> bytes: ...  # b"" when the download holds no .fit
    def can_push_workouts(self) -> bool: ...
    def push_workout(self, workout_fit: bytes, workout_json: dict, day: date) -> str: ...
    def delete_workout(self, workout_id: str) -> None: ...


def state_path() -> Path:
    return storage.DATA_DIR / "garmin_state.json"


def load_state() -> dict:
    path = state_path()
    return json.loads(path.read_text()) if path.exists() else {}


def write_json(path: Path, data: dict) -> None:
    # TODO(v4 merge): take storage.plan_lock / a state lock around read-modify-write of these files.
    storage.write_json(path, json.dumps(data, indent=2))


def secrets_dir() -> Path:
    folder = storage.DATA_DIR / "secrets"
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    return folder


def save_secret(name: str, value: str) -> None:
    descriptor = os.open(secrets_dir() / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(value)
    (secrets_dir() / name).chmod(0o600)  # O_CREAT's mode doesn't reach a file that already existed


def has_secret(name: str) -> bool:
    return (storage.DATA_DIR / "secrets" / name).exists()


def safe_text(error: Exception) -> str:
    """Never str(error): HTTP errors embed the response body."""
    if isinstance(error, GarminError):
        return str(error)
    name = type(error).__name__
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    if "Authentication" in name or status in (401, 403):
        return f"Garmin refused the login or key. {LOGIN_HINT}"
    if "TooManyRequests" in name or status == 429:
        return "Garmin is rate limiting requests. Try again later."
    return f"Garmin request failed ({name}{f' HTTP {status}' if status else ''})."


@contextmanager
def guarded():
    try:
        yield
    except GarminError:
        raise
    except Exception as error:  # third-party clients raise many kinds
        raise GarminError(safe_text(error)) from None


def remote_activity(raw: dict) -> RemoteActivity:
    try:
        local = raw.get(FIELD_START_LOCAL)
        return RemoteActivity(
            id=str(raw[FIELD_ID]),
            start_time=datetime.fromisoformat(raw[FIELD_START_UTC]).replace(tzinfo=timezone.utc),
            sport=(raw.get(FIELD_TYPE) or {}).get(FIELD_TYPE_KEY) or "running",
            distance_m=raw.get(FIELD_DISTANCE_M) or 0,
            duration_s=raw.get(FIELD_DURATION_S) or 0,
            local_start=datetime.fromisoformat(local) if local else None,
            avg_hr=round(raw.get(FIELD_AVG_HR) or 0),
            max_hr=round(raw.get(FIELD_MAX_HR) or 0),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise GarminError(f"Garmin sent an activity in an unexpected shape (fields: {sorted(raw)[:20]}).") from None


def fit_from_download(content: bytes) -> bytes:
    """Garmin's "original file" is a zip, normally of one .fit; a GPX upload stays a GPX inside."""
    if not content.startswith(b"PK"):
        return content
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            member = next((i for i in archive.infolist() if i.filename.lower().endswith(".fit")), None)
            return archive.read(member) if member and member.file_size <= MAX_FIT_BYTES else b""
    except zipfile.BadZipFile:
        return b""


def status_of(connected: bool, can_push: bool) -> GarminStatus:
    state = load_state()
    return GarminStatus(current_mode(), connected, state.get("last_sync_at"), state.get("last_error"), can_push)


class NullProvider:
    def status(self) -> GarminStatus:
        return status_of(False, False)

    def list_activities(self, since: datetime) -> list[RemoteActivity]:
        raise GarminError("Garmin is not set up.")

    def download_fit(self, remote_id: str) -> bytes:
        raise GarminError("Garmin is not set up.")

    def can_push_workouts(self) -> bool:
        return False

    def push_workout(self, workout_fit: bytes, workout_json: dict, day: date) -> str:
        raise NotSupported("Garmin is not set up.")

    def delete_workout(self, workout_id: str) -> None:
        raise NotSupported("Garmin is not set up.")


class PersonalApiProvider(NullProvider):
    """Garmin's official read-only API, through Garmin's own client, so no URLs live here.

    https://developer.garmin.com/gc-developer-program/personal/personal-api/
    The key mints a 24 hour bearer token with no refresh token; the client caches it.
    """

    def __init__(self, session=None):
        self.session = session  # requests.Session; tests pass a fake one

    def status(self) -> GarminStatus:
        return status_of(has_secret(PERSONAL_KEY_FILE), False)

    def api(self, force_refresh: bool = False):
        try:
            from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator
        except ImportError:
            raise GarminError("The garmin-connect-personal-api-python package is not installed.") from None
        if not has_secret(PERSONAL_KEY_FILE):
            raise GarminError(f"No Garmin API key is saved. {LOGIN_HINT}")
        key_file = secrets_dir() / PERSONAL_KEY_FILE
        key_file.chmod(0o600)
        authenticator = PersonalApiAuthenticator(session=self.session, bearer_token_cache_file=secrets_dir() / PERSONAL_BEARER_FILE)
        token = authenticator.obtain_token(key_file.read_text().strip(), force_refresh=force_refresh)
        return GarminPersonalApiClient(token.access_token, session=self.session)

    def call(self, request):
        with guarded():
            try:
                return request(self.api())
            except Exception as error:
                if getattr(error, "status_code", None) != 401:
                    raise
                return request(self.api(force_refresh=True))  # Garmin's docs: a token can die before it expires

    def list_activities(self, since: datetime) -> list[RemoteActivity]:
        found = []
        for page in range(MAX_PAGES):
            reply = self.call(lambda api: api.search_activities(start=page * PAGE_SIZE, limit=PAGE_SIZE, start_date=since.date()))
            # Garmin's own client accepts a bare list or either wrapper here.
            records = reply if isinstance(reply, list) else (reply or {}).get("activities") or (reply or {}).get("activityList") or []
            found += [remote_activity(raw) for raw in records]
            if len(records) < PAGE_SIZE:
                break
        return [activity for activity in found if activity.start_time >= since]

    def download_fit(self, remote_id: str) -> bytes:
        return fit_from_download(self.call(lambda api: api.download_activity(remote_id)))


class UnofficialProvider:
    """python-garminconnect: the mobile app's login, not sanctioned by Garmin. Opt-in only."""

    def __init__(self):
        self.garmin = None

    def status(self) -> GarminStatus:
        return status_of(has_secret(UNOFFICIAL_TOKEN_FILE), True)

    def client(self):
        if self.garmin is None:
            try:
                from garminconnect import Garmin
            except ImportError:
                raise GarminError("The garminconnect package is not installed.") from None
            if not has_secret(UNOFFICIAL_TOKEN_FILE):
                raise GarminError(f"Not logged in to Garmin. {LOGIN_HINT}")
            garmin = Garmin()
            garmin.login(tokenstore=str(secrets_dir()))  # with no credentials this only resumes saved tokens
            self.garmin = garmin
        return self.garmin

    def list_activities(self, since: datetime) -> list[RemoteActivity]:
        with guarded():
            raw = self.client().get_activities_by_date(since.date().isoformat(), None, None, "asc")
        return [activity for activity in map(remote_activity, raw) if activity.start_time >= since]

    def download_fit(self, remote_id: str) -> bytes:
        with guarded():
            client = self.client()
            return fit_from_download(client.download_activity(remote_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL))

    def can_push_workouts(self) -> bool:
        return True

    def push_workout(self, workout_fit: bytes, workout_json: dict, day: date) -> str:
        with guarded():
            client = self.client()
            workout_id = client.upload_workout(workout_json)["workoutId"]
            try:
                client.schedule_workout(workout_id, day.isoformat())
            except Exception:
                with suppress(Exception):  # don't leave an unscheduled copy behind for each retry
                    client.delete_workout(workout_id)
                raise
            try:
                client.push_workout_to_device(workout_id)
            except Exception as error:  # the calendar entry still reaches the watch at its next sync
                log.warning("Garmin device push failed: %s", safe_text(error))
            return str(workout_id)

    def delete_workout(self, workout_id: str) -> None:
        with guarded():
            self.client().delete_workout(workout_id)


def current_mode() -> str:
    return "off" if os.environ.get("OPENSTRIDE_DEMO") else settings.garmin_mode


def make_provider() -> GarminProvider:
    return {"personal_api": PersonalApiProvider, "unofficial": UnofficialProvider}.get(current_mode(), NullProvider)()
