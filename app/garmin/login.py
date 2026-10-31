"""Logging in for Send to Garmin. The password lives only in this login's memory; only tokens are saved."""
import threading
import time

from app.garmin import provider
from app.garmin.provider import GarminError

MFA_WINDOW_S = 300

lock = threading.Lock()
pending = None  # (client, its MFA state, deadline): the state holds session cookies, so memory only


def clear() -> None:
    global pending
    with lock:
        pending = None


def refused_text(error: Exception) -> str:
    if "Authentication" in type(error).__name__:
        return "Garmin refused that email, password or code."
    return provider.safe_text(error)


def save(client) -> None:
    client.password = None
    with provider.guarded():
        client.client.dump(str(provider.token_dir()))


def start(email: str, password: str) -> str:
    """Returns "connected", or "needs_mfa" when Garmin wants a code."""
    global pending
    try:
        from garminconnect import Garmin
    except ImportError:
        raise GarminError("The garminconnect package is not installed.") from None
    client = Garmin(email, password, return_on_mfa=True)
    try:
        status, state = client.login()
    except Exception as error:
        raise GarminError(refused_text(error)) from None
    client.password = None
    if status == "needs_mfa":
        with lock:
            pending = (client, state, time.monotonic() + MFA_WINDOW_S)
        return status
    save(client)
    return "connected"


def finish(code: str) -> str:
    global pending
    with lock:
        waiting, pending = pending, None
    if not waiting or time.monotonic() > waiting[2]:
        raise GarminError("That login has timed out. Log in again.")
    client, state, _ = waiting
    try:
        client.resume_login(state, code)
    except Exception as error:
        raise GarminError(refused_text(error)) from None
    save(client)
    return "connected"
