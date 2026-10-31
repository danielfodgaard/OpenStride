"""One settings object, read once from the environment and .env."""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from dotenv import load_dotenv

load_dotenv()

GarminMode = Literal["off", "personal_api", "unofficial"]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    password: str  # empty means no login
    allow_open: bool  # listen beyond localhost with no password anyway
    garmin_mode: GarminMode
    garmin_sync_minutes: int  # 0 means manual only


def load() -> Settings:
    garmin_mode = os.environ.get("GARMIN_MODE") or "off"
    if garmin_mode not in get_args(GarminMode):
        raise ValueError(f"GARMIN_MODE must be one of {', '.join(get_args(GarminMode))}, not {garmin_mode!r}")
    return Settings(
        host=os.environ.get("OPENSTRIDE_HOST") or "127.0.0.1",
        port=int(os.environ.get("OPENSTRIDE_PORT") or 8000),
        data_dir=Path(os.environ.get("OPENSTRIDE_DATA_DIR") or Path(__file__).parent.parent / "data"),
        password=os.environ.get("OPENSTRIDE_PASSWORD") or "",
        allow_open=os.environ.get("OPENSTRIDE_ALLOW_OPEN") == "1",
        garmin_mode=garmin_mode,
        garmin_sync_minutes=int(os.environ.get("GARMIN_SYNC_MINUTES") or 0),
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


def open_server_problem() -> str | None:
    """Why the server must not start, if it would be open to the network with no password."""
    host = uvicorn_host() or settings.host
    if host in ("localhost", "::1") or host.startswith("127.") or settings.password or settings.allow_open:
        return None
    return (
        f"Refusing to start: listening on {host} with no OPENSTRIDE_PASSWORD. This server holds your "
        "API key, so anyone who can reach it can spend your money. Set OPENSTRIDE_PASSWORD, or stay on "
        "127.0.0.1 and use Tailscale (see the README), or set OPENSTRIDE_ALLOW_OPEN=1 to accept the risk."
    )
