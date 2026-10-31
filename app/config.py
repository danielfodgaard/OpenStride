"""One settings object, read once from the environment and .env."""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from dotenv import load_dotenv

load_dotenv()

GarminSync = Literal["off", "personal_api"]
GarminPush = Literal["off", "unofficial"]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    password: str  # empty means no login
    allow_open: bool  # listen beyond localhost with no password anyway
    garmin_sync: GarminSync  # reading activities: Garmin's official Personal API
    garmin_push: GarminPush  # sending workouts: python-garminconnect, opt-in
    garmin_sync_minutes: int  # 0 means manual only
    garmin_mode_used: bool = False  # the old GARMIN_MODE filled in a setting


def load() -> Settings:
    # GARMIN_MODE is the v4.0 name for the pair; the two settings win where they are set.
    old_mode = os.environ.get("GARMIN_MODE")
    garmin_mode = old_mode or "off"
    if garmin_mode not in ("off", "personal_api", "unofficial"):
        raise ValueError(f"GARMIN_MODE must be off, personal_api or unofficial, not {garmin_mode!r}")
    garmin_sync = os.environ.get("GARMIN_SYNC") or ("off" if garmin_mode == "off" else "personal_api")
    garmin_push = os.environ.get("GARMIN_PUSH") or ("unofficial" if garmin_mode == "unofficial" else "off")
    if garmin_sync not in get_args(GarminSync):
        raise ValueError(f"GARMIN_SYNC must be one of {', '.join(get_args(GarminSync))}, not {garmin_sync!r}")
    if garmin_push not in get_args(GarminPush):
        raise ValueError(f"GARMIN_PUSH must be one of {', '.join(get_args(GarminPush))}, not {garmin_push!r}")
    return Settings(
        host=os.environ.get("OPENSTRIDE_HOST") or "127.0.0.1",
        port=int(os.environ.get("OPENSTRIDE_PORT") or 8000),
        data_dir=Path(os.environ.get("OPENSTRIDE_DATA_DIR") or Path(__file__).parent.parent / "data"),
        password=os.environ.get("OPENSTRIDE_PASSWORD") or "",
        allow_open=os.environ.get("OPENSTRIDE_ALLOW_OPEN") == "1",
        garmin_sync=garmin_sync,
        garmin_push=garmin_push,
        garmin_sync_minutes=int(os.environ.get("GARMIN_SYNC_MINUTES") or 0),
        garmin_mode_used=bool(old_mode) and not (os.environ.get("GARMIN_SYNC") and os.environ.get("GARMIN_PUSH")),
    )


settings = load()


def uvicorn_host() -> str | None:
    """`uvicorn app.main:app --host ...` never reaches the app, so read it where uvicorn does."""
    arguments = sys.argv[1:]
    for index, argument in enumerate(arguments):
        if argument.startswith("--host="):
            return argument.split("=", 1)[1]
        if argument == "--host" and index + 1 < len(arguments):
            return arguments[index + 1]
    return os.environ.get("UVICORN_HOST")


def is_loopback(host: str) -> bool:
    return host in ("localhost", "::1") or host.startswith("127.")


def exposed_without_password() -> bool:
    """Unlike the start-up refusal, OPENSTRIDE_ALLOW_OPEN doesn't excuse this."""
    return not is_loopback(uvicorn_host() or settings.host) and not settings.password


def open_server_problem() -> str | None:
    """Why the server must not start, if it would be open to the network with no password."""
    host = uvicorn_host() or settings.host
    if is_loopback(host) or settings.password or settings.allow_open:
        return None
    return (
        f"Refusing to start: listening on {host} with no OPENSTRIDE_PASSWORD. This server holds your "
        "API key, so anyone who can reach it can spend your money. Set OPENSTRIDE_PASSWORD, or stay on "
        "127.0.0.1 and use Tailscale (see the README), or set OPENSTRIDE_ALLOW_OPEN=1 to accept the risk."
    )
