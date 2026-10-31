"""B4: the optional password and the refusal to run open."""
import logging
import re
import stat
import sys
from dataclasses import replace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import cli, config, main, storage

PASSWORD = "correct horse"


def settings_with(monkeypatch, **changes):
    monkeypatch.setattr(config, "settings", replace(config.settings, **changes))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY_S", 0)
    settings_with(monkeypatch, password=PASSWORD)
    return TestClient(main.app, follow_redirects=False)


def log_in(client, password=PASSWORD, **headers):
    return client.post("/login", data={"password": password}, headers=headers)


def api_routes():
    """From the app itself, so a route added later is covered without a test edit.

    Newer FastAPI keeps an included router as one opaque entry in app.routes, which hides the
    Garmin routes there; the OpenAPI schema lists them, and app.routes adds any left out of it.
    """
    direct = [(route.path, route.methods) for route in main.app.routes if isinstance(route, APIRoute)]
    schema = [(path, operations) for path, operations in main.app.openapi()["paths"].items()]
    found = {
        (method.upper(), re.sub(r"\{\w+\}", "1", path))
        for path, methods in direct + schema
        for method in methods
        if path.startswith("/api/") and path != "/api/health"
    }
    return sorted(found)


def test_the_route_list_reaches_everything_that_matters():
    routes = {path for _, path in api_routes()}
    assert {"/api/garmin/status", "/api/garmin/sync", "/api/plans/1/accept", "/api/backup"} <= routes
    assert "/api/plans/1/sessions/1/1/workout.fit" in routes and len(routes) > 30


@pytest.mark.parametrize("method, path", api_routes())
def test_every_api_route_needs_the_cookie(client, method, path):
    response = client.request(method, path)
    assert response.status_code == 401, (method, path)
    assert response.json() == {"error": "unauthorized"}


@pytest.mark.parametrize("cookie", ["", "junk", "9999999999.", "9999999999.abc", "٣.abc", "é.é", "1.2.3"])
def test_a_forged_cookie_is_refused(client, cookie):
    header = f"{main.SESSION_COOKIE}={cookie}".encode()
    assert client.get("/api/plans", headers={b"cookie": header}).status_code == 401


def test_an_expired_cookie_is_refused(client, monkeypatch):
    client.cookies.set(main.SESSION_COOKIE, f"100.{main.session_signature('100')}")
    assert client.get("/api/plans").status_code == 401


def test_a_cookie_from_before_a_password_change_is_refused(client, monkeypatch):
    log_in(client)
    assert client.get("/api/plans").status_code == 200
    settings_with(monkeypatch, password="new password")
    assert client.get("/api/plans").status_code == 401


def test_html_navigation_redirects_to_login(client):
    for path in ("/", "/index.html", "/docs"):
        response = client.get(path, headers={"accept": "text/html,application/xhtml+xml"})
        assert response.status_code == 303 and response.headers["location"] == "/login", path


def test_an_api_route_opened_in_a_browser_is_still_401(client):
    assert client.get("/api/plans", headers={"accept": "text/html"}).status_code == 401


def test_the_app_shell_is_not_public_but_its_assets_are(client):
    assert client.get("/").status_code == 401
    for path in ("/app.js", "/render/ui.js", "/login"):
        assert client.get(path).status_code == 200, path
    # Track A's files don't exist in this worktree: 404 means the middleware let them through.
    for path in ("/sw.js", "/manifest.webmanifest", "/style.css", "/icons/192.png", "/fonts/barlow.woff2"):
        assert client.get(path).status_code in (200, 404), path


@pytest.mark.parametrize("path", ["/api/plans/x.js", "/api/plans/x.png", "/api/backup.css", "/api/plans/1/workout.fit"])
def test_an_asset_suffix_does_not_open_an_api_path(client, path):
    assert client.get(path).status_code == 401


def test_health_needs_no_login(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_right_password_opens_the_app(client):
    response = log_in(client)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert client.get("/api/plans").status_code == 200
    assert client.get("/").status_code == 200


def test_cookie_flags_on_plain_http(client):
    cookie = log_in(client).headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" not in cookie
    assert "Max-Age=2592000" in cookie


def test_cookie_is_secure_behind_a_tls_proxy(client):
    assert "Secure" in log_in(client, **{"X-Forwarded-Proto": "https"}).headers["set-cookie"]


def test_cookie_is_secure_on_direct_https(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    settings_with(monkeypatch, password=PASSWORD)
    secure = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    assert "Secure" in log_in(secure).headers["set-cookie"]


def test_wrong_password_waits_logs_and_never_logs_the_password(client, monkeypatch, caplog):
    naps = []
    monkeypatch.setattr(main.time, "sleep", naps.append)
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY_S", 1)
    with caplog.at_level(logging.INFO):
        response = log_in(client, "hunter2 not it")
    assert response.status_code == 303 and response.headers["location"] == "/login?failed=1"
    assert "set-cookie" not in response.headers and naps == [1]
    assert "Failed login" in caplog.text and "hunter2" not in caplog.text and PASSWORD not in caplog.text
    assert client.get("/api/plans").status_code == 401


def test_non_ascii_password_attempt_is_just_wrong(client):
    assert log_in(client, "pässwörd").headers["location"] == "/login?failed=1"


def test_no_password_means_no_login(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    settings_with(monkeypatch, password="")
    open_client = TestClient(main.app, follow_redirects=False)
    assert open_client.get("/api/plans").status_code == 200
    assert "set-cookie" not in log_in(open_client, "").headers


def test_session_secret_is_private_and_survives(client):
    first = storage.session_secret()
    path = storage.DATA_DIR / "secrets" / "session_secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(first) == 32
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert storage.session_secret() == first


# Refusing to run open

def open_to_the_network(monkeypatch, **changes):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(sys, "argv", ["openstride"])
    settings_with(monkeypatch, **({"host": "0.0.0.0", "password": "", "allow_open": False} | changes))


def starts(monkeypatch=None):
    with TestClient(main.app):
        return True


def test_lifespan_refuses_all_interfaces_without_a_password(monkeypatch):
    open_to_the_network(monkeypatch)
    with pytest.raises(RuntimeError, match="OPENSTRIDE_PASSWORD"):
        starts()


def test_console_script_refuses_and_never_starts_uvicorn(monkeypatch):
    open_to_the_network(monkeypatch)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("started"))
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert "OPENSTRIDE_ALLOW_OPEN" in str(stopped.value.code)


@pytest.mark.parametrize("argv", [
    ["uvicorn", "app.main:app", "--host", "0.0.0.0"],
    ["uvicorn", "app.main:app", "--host=0.0.0.0"],
])
def test_uvicorn_host_flag_is_refused_too(monkeypatch, argv):
    open_to_the_network(monkeypatch, host="127.0.0.1")
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(RuntimeError, match="0.0.0.0"):
        starts()


def test_uvicorn_host_environment_variable_is_refused_too(monkeypatch):
    open_to_the_network(monkeypatch, host="127.0.0.1")
    monkeypatch.setenv("UVICORN_HOST", "192.168.1.20")
    with pytest.raises(RuntimeError, match="192.168.1.20"):
        starts()


@pytest.mark.parametrize("changes", [
    {"password": PASSWORD},
    {"allow_open": True},
    {"host": "127.0.0.1"},
    {"host": "localhost"},
    {"host": "::1"},
])
def test_these_start(monkeypatch, changes):
    open_to_the_network(monkeypatch, **changes)
    assert starts()


def test_api_key_is_still_required(monkeypatch):
    open_to_the_network(monkeypatch, host="127.0.0.1")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        starts()


def test_allow_open_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_ALLOW_OPEN", "1")
    assert config.load().allow_open is True
    monkeypatch.setenv("OPENSTRIDE_ALLOW_OPEN", "")
    assert config.load().allow_open is False
