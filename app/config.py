"""One settings object, read once from the environment and .env. Secrets live here and nowhere in the workspace."""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Inside an agent's shell the CLI must not read the server's .env: the keys stay with the server.
IN_AGENT_SHELL = bool(os.environ.get("OPENSTRIDE_TURN"))
if not IN_AGENT_SHELL:
    try:
        load_dotenv()
    except OSError:
        pass

# Never handed to an agent's shell: the runtime blanks these in the subprocess environment.
SECRET_ENV = ("INTERVALS_ICU_API_KEY", "OPENSTRIDE_PASSWORD", "TS_AUTHKEY")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    password: str  # empty means no login
    allow_open: bool  # listen beyond localhost with no password anyway
    provider: str  # which analyzer holds the record: intervals_icu, demo or none
    icu_athlete_id: str
    icu_api_key: str
    runtime: str  # claude, or scripted for the offline demo and tests
    sandbox: str  # auto, on, weak or off: OS sandboxing of the agents' shells

    @property
    def workspace(self) -> Path:
        given = os.environ.get("OPENSTRIDE_WORKSPACE")  # set by the runtime for the CLI inside an agent's shell
        return Path(given) if given and IN_AGENT_SHELL else self.data_dir / "workspace"

    @property
    def state(self) -> Path:
        return self.data_dir / "state"


def load() -> Settings:
    key = os.environ.get("INTERVALS_ICU_API_KEY") or ""
    demo = bool(os.environ.get("OPENSTRIDE_DEMO"))
    provider = os.environ.get("OPENSTRIDE_PROVIDER") or ("demo" if demo else "intervals_icu" if key else "none")
    if provider not in ("intervals_icu", "demo", "none"):
        raise ValueError(f"OPENSTRIDE_PROVIDER must be intervals_icu, demo or none, not {provider!r}")
    sandbox = os.environ.get("OPENSTRIDE_SANDBOX") or "auto"
    if sandbox not in ("auto", "on", "weak", "off"):
        raise ValueError(f"OPENSTRIDE_SANDBOX must be auto, on, weak or off, not {sandbox!r}")
    return Settings(
        host=os.environ.get("OPENSTRIDE_HOST") or "127.0.0.1",
        port=int(os.environ.get("OPENSTRIDE_PORT") or 8000),
        data_dir=Path(
            os.environ.get("OPENSTRIDE_DATA_DIR")
            or (Path(os.environ["OPENSTRIDE_WORKSPACE"]).parent if os.environ.get("OPENSTRIDE_WORKSPACE") else Path(__file__).parent.parent / "data")
        ).resolve(),
        password=os.environ.get("OPENSTRIDE_PASSWORD") or "",
        allow_open=os.environ.get("OPENSTRIDE_ALLOW_OPEN") == "1",
        provider=provider,
        icu_athlete_id=os.environ.get("INTERVALS_ICU_ATHLETE_ID") or "0",  # 0 is "the key's own athlete"
        icu_api_key=key,
        runtime=os.environ.get("OPENSTRIDE_RUNTIME") or ("scripted" if demo else "claude"),
        sandbox=sandbox,
    )


settings = load()


def reload() -> Settings:
    """For tests and the demo, which change the environment after import."""
    global settings
    settings = load()
    return settings


def uvicorn_host() -> str | None:
    """`uvicorn app.server:app --host ...` never reaches the app, so read it where uvicorn does."""
    arguments = sys.argv[1:]
    for index, argument in enumerate(arguments):
        if argument.startswith("--host="):
            return argument.split("=", 1)[1]
        if argument == "--host" and index + 1 < len(arguments):
            return arguments[index + 1]
    return os.environ.get("UVICORN_HOST")


def is_loopback(host: str) -> bool:
    return host in ("localhost", "::1") or host.startswith("127.")


def open_server_problem() -> str | None:
    """Why the server must not start, if it would be open to the network with no password."""
    host = uvicorn_host() or settings.host
    if is_loopback(host) or settings.password or settings.allow_open:
        return None
    return (
        f"Refusing to start: listening on {host} with no OPENSTRIDE_PASSWORD. This server holds your "
        "API keys, so anyone who can reach it can spend your money. Set OPENSTRIDE_PASSWORD, or stay on "
        "127.0.0.1 and use Tailscale (see docs/SETUP.md), or set OPENSTRIDE_ALLOW_OPEN=1 to accept the risk."
    )
