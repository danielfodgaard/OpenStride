"""A runtime with no model: a function decides what the agent says and does.

Tests use it to drive the real turn loop, boundary and gate; the offline demo uses it to answer from
the sample data without an API key. Tool steps go through the same boundary check as a real agent's.
"""
from typing import Callable, Iterable

from app.runtime import hooks
from app.runtime.base import TurnRequest

Step = tuple  # ("say", text) or ("tool", name, input, action) where action() performs the call


class Scripted:
    name = "scripted"

    def __init__(self, script: Callable[[TurnRequest], Iterable[Step]]):
        self.script = script
        self.requests: list[TurnRequest] = []

    async def run(self, request: TurnRequest):
        self.requests.append(request)
        error = ""
        try:
            for step in self.script(request):
                if step[0] == "say":
                    yield ("delta", step[1])
                    yield ("text", step[1])
                elif step[0] == "tool":
                    _, name, tool_input, action = step
                    reason = hooks.check(request.policy, name, tool_input)
                    if reason:
                        yield ("denied", reason)
                        continue
                    yield ("tool", {"name": name, "input": tool_input})
                    if action:
                        action()
        except Exception as failure:
            error = f"{type(failure).__name__}: {failure}"
        yield ("result", {"session_id": request.session_id or "scripted", "usage": {}, "cost_usd": 0.0, "error": error, "turns": 1})
