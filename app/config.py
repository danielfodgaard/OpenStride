"""One settings object, read once from the environment and .env."""
import os
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
        garmin_mode=garmin_mode,
        garmin_sync_minutes=int(os.environ.get("GARMIN_SYNC_MINUTES") or 0),
    )


settings = load()
