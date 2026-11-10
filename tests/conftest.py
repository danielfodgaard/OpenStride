import asyncio
import json
import os
import time
from pathlib import Path

import pytest

# Tests assume Singapore local dates, so they give the same answer on any machine.
os.environ["TZ"] = "Asia/Singapore"
time.tzset()

# Before any app import, so a developer's .env can't reach the tests.
os.environ |= {
    "OPENSTRIDE_PASSWORD": "", "INTERVALS_ICU_API_KEY": "", "OPENSTRIDE_PROVIDER": "demo", "OPENSTRIDE_RUNTIME": "scripted",
    "OPENSTRIDE_NO_AGENT_SPOOL": "1", "OPENSTRIDE_NO_TRIGGERS": "1", "OPENSTRIDE_SANDBOX": "off",
}
os.environ.pop("OPENSTRIDE_DEMO", None)

ROOT = Path(__file__).parent.parent
TODAY = "2026-11-25"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own data folder and clock, and no real API key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENSTRIDE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENSTRIDE_TODAY", TODAY)
    from app import config, providers
    from app.hub import hub
    from app.stream import Stream

    config.reload()
    providers.use(None)
    hub.stream, hub.agent, hub.runtime, hub.tokens, hub.loop = Stream(), None, None, {}, None
    yield
    providers.use(None)


@pytest.fixture
def ws():
    """An empty workspace."""
    from app import workspace

    workspace.init()
    return workspace


@pytest.fixture
def demo_ws(ws):
    """The sample block: a ten-week plan, three and a half weeks in, synced from the fixture provider."""
    from app import migrate, mirror

    migrate.run(ROOT / "examples" / "demo" / "data")
    mirror.sync()
    return ws


def set_today(monkeypatch, day: str) -> None:
    monkeypatch.setenv("OPENSTRIDE_TODAY", day)


def week_draft(week: int, rationale: str = "Because the athlete asked.") -> dict:
    from app import plan

    return plan.as_draft(plan.load(), rationale, [week])


def session_in(draft: dict, title: str) -> dict:
    return next(s for s in draft["weeks"][0]["sessions"] if s["title"] == title)


def approve(proposal):
    from app import plan

    return plan.apply(proposal.id, plan.approval_token(proposal))


def run(coroutine):
    return asyncio.run(coroutine)


def read_json(path: Path):
    return json.loads(path.read_text())
