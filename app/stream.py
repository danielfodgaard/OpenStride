"""The stream: everything the athlete sees, in order. Messages, cards and approvals are kept in an
append-only file; status lines and the words of a reply as they are written pass through and are gone.
"""
import asyncio
import json
import threading

from app import workspace

PERSISTED = {"message", "card", "proposal", "notice", "turn"}


class Stream:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next = None

    @property
    def path(self):
        return workspace.state() / "stream.jsonl"

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _count(self) -> int:
        if self._next is None:
            self._next = sum(1 for _ in open(self.path)) + 1 if self.path.exists() else 1
        return self._next

    def append(self, kind: str, **fields) -> dict:
        """Add an event the athlete will still see tomorrow."""
        assert kind in PERSISTED, kind
        with self._lock:
            event = {"id": self._count(), "at": workspace.now_iso(), "type": kind} | fields
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as file:
                file.write(json.dumps(event) + "\n")
            self._next += 1
        self._send(event)
        return event

    def publish(self, kind: str, **fields) -> None:
        """Send an event to whoever is watching now; it is not kept."""
        self._send({"type": kind} | fields)

    def _send(self, event: dict) -> None:
        loop = self._loop
        if loop is None or not self._subscribers:
            return

        def deliver():
            for queue in list(self._subscribers):
                queue.put_nowait(event)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            deliver()
        else:
            loop.call_soon_threadsafe(deliver)

    def history(self, after: int = 0, before: int | None = None, limit: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        events = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        events = [e for e in events if e["id"] > after and (before is None or e["id"] < before)]
        return events[-limit:] if not after else events[:limit]

    def last(self, kind: str, **match) -> dict | None:
        for event in reversed(self.history(limit=10**9)):
            if event["type"] == kind and all(event.get(k) == v for k, v in match.items()):
                return event
        return None

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
