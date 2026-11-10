"""What the running server shares: the stream, the agent, and which token belongs to which agent."""
import asyncio
import json
import secrets
from datetime import datetime

from app import clock, workspace
from app.stream import Stream


class Hub:
    def __init__(self):
        self.stream = Stream()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.agent = None  # app.agent.Agent
        self.runtime = None
        self.tokens: dict[str, str] = {}  # token -> role

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.stream.bind(loop)

    def token_for(self, role: str) -> str:
        token = secrets.token_urlsafe(24)
        self.tokens[token] = role
        return token

    def role_of(self, token: str) -> str | None:
        return self.tokens.get(token or "")


hub = Hub()


def usage_path():
    return workspace.state() / "usage.jsonl"


def record_usage(**fields) -> None:
    usage_path().parent.mkdir(parents=True, exist_ok=True)
    with open(usage_path(), "a") as file:
        file.write(json.dumps({"at": workspace.now_iso()} | fields) + "\n")


def usage(month: str | None = None) -> dict:
    """What the agents have cost: this month by default, as the harness reports it."""
    month = month or f"{clock.today():%Y-%m}"
    rows = []
    if usage_path().exists():
        rows = [json.loads(line) for line in usage_path().read_text().splitlines() if line.strip()]
    rows = [row for row in rows if row["at"].startswith(month)]
    tokens = {}
    for row in rows:
        for key, value in (row.get("usage") or {}).items():
            if isinstance(value, (int, float)):
                tokens[key] = tokens.get(key, 0) + value
    return {
        "month": month, "turns": len(rows), "cost_usd": round(sum(row.get("cost_usd") or 0 for row in rows), 4),
        "tokens": tokens, "by_role": {role: round(sum(r.get("cost_usd") or 0 for r in rows if r.get("role") == role), 4) for role in {r.get("role") for r in rows}},
    }


def over_budget() -> bool:
    cap = workspace.settings()["budget"]["monthly_usd"]
    return bool(cap) and usage()["cost_usd"] >= cap


def local_now() -> datetime:
    return clock.now()
