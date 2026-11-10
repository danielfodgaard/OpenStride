"""The offline demo: a sample half-marathon block, three and a half weeks in, with no API key.

It is the old sample data brought in the same way a real upgrade is (migrate), a provider that reads
fixture files, and a scripted stand-in for the agent that answers from the cards.
"""
import os
from pathlib import Path

from app import config, migrate, mirror, workspace

LEGACY = Path(__file__).parent.parent / "examples" / "demo" / "data"
TODAY = "2026-11-25"
WELCOME = (
    "This is the offline demo: a sample half-marathon plan, three and a half weeks in, with made-up runs. "
    "There is no model behind it, so I answer from a script. Try **this week**, **the plan**, **fitness**, "
    "**volume**, **my last run**, or **move my next easy run** to see a proposal and its approval card."
)


def prepare(folder: Path) -> None:
    os.environ["OPENSTRIDE_DATA_DIR"] = str(folder)
    os.environ["OPENSTRIDE_TODAY"] = TODAY
    os.environ.setdefault("OPENSTRIDE_DEMO", "1")
    config.reload()
    workspace.init()
    migrate.run(LEGACY)
    mirror.sync()


def seed(hub) -> None:
    """The first things in the stream, once."""
    if hub.stream.history(limit=1):
        return
    hub.stream.append("message", role="coach", text=WELCOME, origin="system")
    hub.stream.append("card", card="week", args={"week": "this"}, data=None, by="coach")
