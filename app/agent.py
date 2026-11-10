"""The interface agent's turns: one at a time, on one persistent session.

A message from the athlete and a brief from a trigger are the same thing here: an item on the queue.
After every turn the boundary is enforced, the workspace is committed and the cost is recorded.
"""
import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app import clock, config, hub as hub_module, workspace
from app.runtime.base import TurnRequest, describe
from app.runtime.hooks import Policy

log = logging.getLogger(__name__)
GUIDE_DIR = Path(__file__).parent / "guide"
SILENT = "SILENT"


@dataclass
class Item:
    kind: str  # message or brief
    text: str
    origin: str  # athlete, or the trigger's name
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    effort: str = ""
    created_at: str = field(default_factory=workspace.now_iso)


def coach_policy() -> Policy:
    state = workspace.state()
    return Policy(role="coach", workspace=workspace.root(), cwd=workspace.root(), secrets=(str(state), "approval_key", "session_secret"))


def system_prompt(role: str) -> str:
    return (GUIDE_DIR / f"{role.upper()}.md").read_text() + "\n\n" + workspace.guide_text()


class Agent:
    def __init__(self, hub, runtime):
        self.hub = hub
        self.runtime = runtime
        self.pending: list[Item] = []
        self.wake = asyncio.Event()
        self.current: Item | None = None
        self.task: asyncio.Task | None = None
        self.turn_task: asyncio.Task | None = None
        self.commit_message = ""
        self.idle = asyncio.Event()
        self.idle.set()
        self.pending = [Item(**row) for row in self._read("queue.json", [])]
        if self.pending:
            self.wake.set()

    # --- small state files -------------------------------------------------------------------
    def _read(self, name: str, default):
        path = workspace.state() / name
        try:
            return json.loads(path.read_text()) if path.exists() else default
        except ValueError:
            return default

    def _write(self, name: str, value) -> None:
        workspace.write_file(workspace.state() / name, json.dumps(value, indent=1))

    def _save_queue(self) -> None:
        self._write("queue.json", [asdict(item) for item in self.pending])

    def add_notice(self, text: str) -> None:
        """Something the agent should know at the start of its next turn."""
        self._write("notices.json", [*self._read("notices.json", []), text])

    def take_notices(self) -> list[str]:
        notices = self._read("notices.json", [])
        if notices:
            self._write("notices.json", [])
        return notices

    # --- the queue ---------------------------------------------------------------------------
    def enqueue(self, kind: str, text: str, origin: str, effort: str = "") -> Item:
        item = Item(kind=kind, text=text, origin=origin, effort=effort)
        self.pending.append(item)
        self._save_queue()
        self.idle.clear()
        self.wake.set()
        self.hub.stream.publish("busy", busy=True)
        return item

    def busy(self) -> bool:
        return self.current is not None or bool(self.pending)

    def start(self) -> None:
        self.task = asyncio.create_task(self.worker())

    async def stop(self) -> None:
        for task in (self.turn_task, self.task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self.turn_task, self.task) if t), return_exceptions=True)

    def interrupt(self) -> bool:
        """Stop the turn in progress, at the athlete's word."""
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()
            return True
        return False

    async def worker(self) -> None:
        while True:
            await self.wake.wait()
            while self.pending:
                self.current = self.pending.pop(0)
                self._save_queue()
                self.turn_task = asyncio.create_task(self.turn(self.current))
                try:
                    await self.turn_task
                except asyncio.CancelledError:
                    if not self.turn_task.cancelled():
                        raise  # the worker itself is being stopped
                    self.hub.stream.append("turn", state="stopped", turn=self.current.id, origin=self.current.origin)
                except Exception:
                    log.exception("A turn failed")
                    self.hub.stream.append("turn", state="failed", turn=self.current.id, origin=self.current.origin, error="The turn failed unexpectedly. See the server log.")
                finally:
                    await asyncio.to_thread(self.finish, self.current)
                    self.current = None
                    self.hub.stream.publish("busy", busy=bool(self.pending))
            self.wake.clear()
            self.idle.set()

    # --- one turn ----------------------------------------------------------------------------
    def compose(self, item: Item) -> str:
        today = clock.today()
        lines = [f"Today is {today:%A} {today.isoformat()}."]
        notices = self.take_notices()
        if notices:
            lines += ["", "[since your last turn]", *(f"- {notice}" for notice in notices)]
        lines.append("")
        lines.append(item.text if item.kind == "message" else f"[brief: {item.origin}]\n{item.text}")
        return "\n".join(lines)

    def request(self, item: Item) -> TurnRequest:
        settings = workspace.settings()
        effort = item.effort or settings["effort"]["main" if item.kind == "message" else "triggers"]
        token = self.hub.token_for("coach")
        return TurnRequest(
            prompt=self.compose(item),
            system_prompt=system_prompt("coach"),
            policy=coach_policy(),
            session_id=self._read("session.json", {}).get("coach"),
            model=settings["models"]["main"],
            helper_model=settings["models"]["helper"],
            effort=effort,
            token=token,
            env={"OPENSTRIDE_AGENT_SOCKET": self.hub.socket},
        )

    async def turn(self, item: Item) -> None:
        stream = self.hub.stream
        stream.append("turn", state="started", turn=item.id, origin=item.origin)
        stream.publish("status", turn=item.id, text="Reading up…")
        self.commit_message = ""
        request = self.request(item)
        texts, result = [], {}
        try:
            for attempt in (1, 2):
                texts, result = [], {}
                async for kind, payload in self.runtime.run(request):
                    if kind == "delta":
                        stream.publish("delta", turn=item.id, text=payload)
                    elif kind == "text":
                        texts.append(payload.strip())
                        stream.publish("delta", turn=item.id, text="\n\n")
                    elif kind == "tool":
                        stream.publish("status", turn=item.id, text=describe(payload["name"], payload["input"], workspace.root()))
                    elif kind == "result":
                        result = payload
                # A session that can't be found (a new machine, a cleared folder) starts fresh, once.
                if attempt == 1 and result.get("error") and request.session_id and not texts:
                    log.warning("Resuming the session failed (%s); starting a new one", result["error"])
                    request.session_id = None
                    stream.publish("reset", turn=item.id)
                    continue
                break
        finally:
            self.hub.tokens.pop(request.token, None)
        if result.get("session_id"):
            self._write("session.json", self._read("session.json", {}) | {"coach": result["session_id"]})
        hub_module.record_usage(role="coach", turn=item.id, origin=item.origin, model=request.model, cost_usd=result.get("cost_usd") or 0, usage=result.get("usage") or {})
        body = "\n\n".join(text for text in texts if text)
        if body and body.strip() != SILENT:
            stream.append("message", role="coach", text=body, turn=item.id, origin=item.origin)
        error = result.get("error") or ""
        stream.append("turn", state="failed" if error else "ended", turn=item.id, origin=item.origin, error=error, cost_usd=round(result.get("cost_usd") or 0, 4))

    def finish(self, item: Item) -> None:
        """After every turn, however it ended: undo what the agent may not write, then commit the rest."""
        try:
            with workspace.lock():
                undone = workspace.enforce()
                if undone:
                    self.add_notice(
                        "You wrote to protected paths and the change was undone: " + ", ".join(undone[:8])
                        + ". plan/ changes only through `openstride plan draft` and the athlete's approval; the record is never written."
                    )
                    log.warning("Undid agent writes to protected paths: %s", undone)
                headline = self.commit_message or f"{item.origin}: {' '.join(item.text.split())[:72]}"
                workspace.commit(headline)
            self.hub.stream.publish("data_changed")
        except Exception:
            log.exception("Finishing a turn failed")


def make_runtime():
    if config.settings.runtime == "scripted":
        from app.runtime.demo import demo_runtime

        return demo_runtime()
    from app.runtime.claude_sdk import ClaudeSdk

    return ClaudeSdk()
