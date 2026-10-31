"""The safeguards around Send to Garmin. Nothing here reaches Garmin."""
import json
import logging
import stat
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import garminconnect
import pytest
from fastapi.testclient import TestClient
from garminconnect import Garmin
from garminconnect.exceptions import GarminConnectAuthenticationError

from app import cli, config, logsafe, main, storage
from app.garmin import login, provider
from conftest import save_secret

PASSWORD = "fake-password-zebra"
TOKEN = "fake-session-token-zebra"
LOCAL = "http://127.0.0.1:8000"
TAILNET = "machine.tailnet.ts.net"


class FakeGarmin:
    """Stands in for garminconnect.Garmin at the login."""

    needs_mfa = False
    fail = None

    def __init__(self, email, password, return_on_mfa=False):
        self.password = password
        self.client = MagicMock()
        self.client.dump.side_effect = lambda folder: storage.write_file(
            Path(folder) / "garmin_tokens.json", json.dumps({"di_refresh_token": TOKEN}), mode=0o600,
        )

    def login(self):
        if self.fail:
            raise self.fail
        logging.getLogger("garminconnect").warning("logging in with password=%s", self.password)
        return ("needs_mfa", {"cookies": TOKEN}) if self.needs_mfa else (None, None)

    def resume_login(self, state, code):
        if code != "123456":
            raise GarminConnectAuthenticationError("bad code")
        return None, None


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used")
    monkeypatch.setattr(provider, "settings", replace(provider.settings, garmin_push="unofficial"))
    monkeypatch.setattr(garminconnect, "Garmin", FakeGarmin)
    monkeypatch.setattr(FakeGarmin, "needs_mfa", False)
    monkeypatch.setattr(FakeGarmin, "fail", None)
    login.clear()


@pytest.fixture
def client():
    with TestClient(main.app, base_url=LOCAL) as test_client:
        yield test_client


def log_in(client, **kwargs):
    return client.post("/api/garmin/login", json={"email": "a@b.c", "password": PASSWORD}, **kwargs)


def token_path():
    return storage.DATA_DIR / "secrets" / provider.UNOFFICIAL_TOKEN_FILE


def test_login_stores_only_a_token_with_tight_permissions(client):
    assert log_in(client).json() == {"status": "connected"}
    assert stat.S_IMODE(token_path().stat().st_mode) == 0o600
    assert stat.S_IMODE(token_path().parent.stat().st_mode) == 0o700
    assert client.get("/api/garmin/status").json()["push_connected"]
    for path in storage.DATA_DIR.rglob("*"):
        assert not path.is_file() or PASSWORD.encode() not in path.read_bytes()


def test_mfa_is_a_second_step_and_its_cookies_stay_in_memory(client, monkeypatch):
    monkeypatch.setattr(FakeGarmin, "needs_mfa", True)
    assert log_in(client).json() == {"status": "needs_mfa"}
    assert not token_path().exists()
    assert client.post("/api/garmin/login/mfa", json={"code": "123456"}).json() == {"status": "connected"}
    assert token_path().exists() and login.pending is None


def test_a_wrong_code_ends_the_login(client, monkeypatch):
    monkeypatch.setattr(FakeGarmin, "needs_mfa", True)
    log_in(client)
    response = client.post("/api/garmin/login/mfa", json={"code": "000000"})
    assert response.status_code == 502 and "refused" in response.json()["detail"]
    assert client.post("/api/garmin/login/mfa", json={"code": "123456"}).status_code == 502
    assert not token_path().exists()


def test_a_code_after_the_window_is_refused(client, monkeypatch):
    monkeypatch.setattr(FakeGarmin, "needs_mfa", True)
    monkeypatch.setattr(login, "MFA_WINDOW_S", -1)
    log_in(client)
    assert "timed out" in client.post("/api/garmin/login/mfa", json={"code": "123456"}).json()["detail"]


def test_a_refused_login_says_so_without_echoing_anything(client, monkeypatch):
    monkeypatch.setattr(FakeGarmin, "fail", GarminConnectAuthenticationError(f"no {PASSWORD}"))
    response = log_in(client)
    assert response.status_code == 502 and PASSWORD not in response.text


@pytest.mark.parametrize("base,headers,allowed", [
    (LOCAL, {}, True),
    ("http://localhost:8000", {}, True),
    (f"https://{TAILNET}", {}, True),
    ("http://127.0.0.1:8000", {"x-forwarded-host": TAILNET, "x-forwarded-proto": "https", "origin": f"https://{TAILNET}"}, True),
    (f"http://{TAILNET}", {}, False),
    ("http://192.168.1.20:8000", {}, False),
    ("https://openstride.example.com", {}, False),
    (LOCAL, {"origin": "https://evil.example.com"}, False),
])
def test_the_login_only_goes_to_this_machine_or_tailscale(base, headers, allowed):
    with TestClient(main.app, base_url=base) as test_client:
        response = log_in(test_client, headers=headers)
        status = test_client.get("/api/garmin/status", headers=headers).json()
    assert (response.status_code == 200) == allowed and status["login_allowed"] == allowed
    assert token_path().exists() == allowed


def expose(monkeypatch, password=""):
    settings = replace(config.settings, host="0.0.0.0", password=password, allow_open=True)
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(config, "uvicorn_host", lambda: None)


def test_an_exposed_server_refuses_sending_and_says_why(client, monkeypatch):
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, "{}")
    expose(monkeypatch)
    body = client.get("/api/garmin/status").json()
    assert not body["can_push_workouts"] and "OPENSTRIDE_PASSWORD" in body["push_blocked"]
    response = log_in(client)
    assert response.status_code == 403 and "OPENSTRIDE_PASSWORD" in response.json()["detail"]
    assert client.post("/api/plans/x/sessions/1/0/send-to-garmin").status_code == 403
    assert client.post("/api/plans/x/weeks/1/send-to-garmin").status_code == 403


def test_a_password_lifts_the_exposure_refusal():
    from app.garmin.provider import push_refusal
    original = config.settings
    try:
        config.settings = replace(original, host="0.0.0.0", password="x")
        assert push_refusal() is None
    finally:
        config.settings = original


def test_disconnect_deletes_the_token_and_turns_sending_off(client):
    log_in(client)
    assert client.post("/api/garmin/disconnect").json() == {"status": "disconnected"}
    assert not token_path().parent.exists()
    body = client.get("/api/garmin/status").json()
    assert not body["push_connected"] and not body["can_push_workouts"]


def test_the_cli_disconnects_too(monkeypatch, capsys):
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, "{}")
    monkeypatch.setattr(sys, "argv", ["openstride", "garmin-disconnect"])
    cli.main()
    assert not token_path().exists() and "deleted" in capsys.readouterr().out


@pytest.mark.parametrize("include", [False, True])
def test_backups_never_hold_the_login_token(tmp_path, include):
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, TOKEN)
    save_secret(provider.PERSONAL_BEARER_FILE, "bearer")
    with zipfile.ZipFile(storage.write_backup(tmp_path / "out", include)) as archive:
        names = archive.namelist()
    assert not any("garmin-unofficial" in name for name in names)
    assert (f"secrets/{provider.PERSONAL_BEARER_FILE}" in names) == include


def test_only_workout_writes_reach_the_unofficial_client(monkeypatch):
    save_secret(provider.UNOFFICIAL_TOKEN_FILE, "{}")
    mocked = create_autospec(Garmin)
    mocked.return_value.login.return_value = (None, None)
    mocked.return_value.upload_workout.return_value = {"workoutId": 7}
    monkeypatch.setattr(garminconnect, "Garmin", mocked)
    pusher = provider.UnofficialProvider()
    assert pusher.push_workout({}, date(2026, 11, 3)) == "7"
    pusher.delete_workout("7")
    called = {call[0] for call in mocked.return_value.mock_calls}
    assert called == {"login", "upload_workout", "schedule_workout", "push_workout_to_device", "delete_workout"}


@pytest.mark.parametrize("line", [
    "Authorization: Bearer abc.def-ghi",
    "headers {'Authorization': 'ApiKey sekret123'}",
    "Cookie: session=abc123",
    "set-cookie=abc123",
    '{"di_refresh_token": "abc123"}',
    "access_token=abc123",
    "password=abc123",
    "password: abc123",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123",
])
def test_redaction_catches_each_shape(line):
    clean = logsafe.redact(line)
    assert "abc" not in clean and "sekret" not in clean and "eyJ" not in clean


def test_ordinary_log_lines_are_left_alone():
    line = 'GET /api/plans/2026-10-31_hm/status HTTP/1.1" 200 OK'
    assert logsafe.redact(line) == line


def test_a_login_and_a_send_log_nothing_secret(client, caplog):
    caplog.set_level(logging.DEBUG)
    logsafe.install()
    logging.getLogger("garminconnect").warning("got %s", f"Bearer {TOKEN} cookie={TOKEN}")
    log_in(client)
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text


def test_noisy_loggers_stay_at_warning():
    for name in logsafe.QUIET_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_the_unofficial_library_is_pinned_exactly():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    pins = dict(item.split("==") for item in pyproject["project"]["dependencies"] if "==" in item)
    assert {"garminconnect", "curl-cffi", "ua-generator", "requests"} <= set(pins)
    locked = {p["name"]: p["version"] for p in tomllib.loads(Path("uv.lock").read_text())["package"]}
    assert all(locked[name] == version for name, version in pins.items())


def test_docs_only_mention_funnel_to_warn_against_it():
    text = Path("docs/SETUP.md").read_text() + Path("README.md").read_text()
    assert text.count("tailscale funnel") == 1 and "Never use `tailscale funnel`" in text


def test_the_token_folder_is_ignored_by_git():
    ignored = subprocess.run(["git", "check-ignore", "-q", "data/secrets/garmin-unofficial/x"], check=False)
    assert ignored.returncode == 0
