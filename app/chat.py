"""The coach: one conversation with two tools. It proposes plan changes and keeps its notes; the plan changes only through accept."""
import json
import os
import uuid
from datetime import datetime, timezone

from app import adapt, clock, generator, lifecycle, logbook, memory, metrics, storage
from app.models import ChatMessage, Usage
from app.prompts.chat import SYSTEM_PROMPT, TOOLS, build_user_prompt

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
HISTORY_LIMIT = 30
RECENT_PROPOSALS = 5
USER_MESSAGES_FOR_ADAPTATION = 3
MAX_CALLS = 6  # a notes update, a proposal and the reply, with room for a tool error and one correction
LEAK_MARKS = ("<invoke", "</invoke>", "</parameter>")  # a tool call written out as text instead of made
LEAK_NUDGE = "That reply wrote a tool call out as text, so nothing was saved and the athlete would see the tags. Make the call through the tool interface now, then give your whole reply again without any tool-call text."


def message(role: str, text: str, **fields) -> ChatMessage:
    return ChatMessage(
        id=uuid.uuid4().hex,
        role=role,
        created_at=lifecycle.now_iso(),
        text=text,
        **fields,
    )


class Turn:
    """The coach's tools during one message, and what they did, so the saved message can point at it."""

    def __init__(self, plan_id: str, client, today, said: list[str]):
        self.plan_id, self.client, self.today, self.said = plan_id, client, today, said
        self.proposal_id = ""
        self.proposed = False
        self.memory_updated = False

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """The tool result text, and whether it is an error."""
        if name == "propose_plan_change":
            return self.propose(args["scope"], args["change_request"])
        if name == "update_notes":
            return self.remember(args["content"])
        return f"There is no tool called {name}.", True

    def propose(self, scope: str, change_request: str) -> tuple[str, bool]:
        if self.proposed:
            return "A proposal was already prepared for this message. Explain it instead.", True
        if not change_request.strip():
            return "change_request must say exactly what to change.", True
        context = change_request + "\n\nFrom chat:\n" + "\n".join(self.said)
        try:
            proposal = adapt.create_proposal(self.plan_id, scope, context, self.client, self.today, origin="chat")
        except (generator.GenerationError, lifecycle.Conflict) as error:
            return f"The proposal could not be prepared: {error}", True
        self.proposed = True
        if proposal.result.recommendation != "no_change":
            self.proposal_id = proposal.proposal_id
        return json.dumps({
            "recommendation": proposal.result.recommendation,
            "rationale": proposal.result.rationale,
            "changes": [f"Week {c.week_number} {c.day}: {c.summary}" for c in proposal.result.changes],
            "acceptable": proposal.acceptable,
            "trade_offs": proposal.warnings,
        }), False

    def remember(self, content: str) -> tuple[str, bool]:
        try:
            memory.save(content, by="coach")
        except ValueError as error:
            return str(error), True
        self.memory_updated = True
        return "Notes saved.", False


def stream(plan_id: str, text: str, client=None, today=None):
    """Yields the coach's words as they arrive; the return value is what `send` returns.

    Both messages are saved together after the reply, so a failed call leaves the thread as it was.
    """
    plan_file = lifecycle.require_active(plan_id)
    today = today or clock.today()
    client = client or generator.make_client()
    model = os.environ.get("CHAT_MODEL") or DEFAULT_MODEL
    user = message("user", text.strip())
    history = [*storage.load_chat(plan_id), user][-HISTORY_LIMIT:]
    prompt = build_user_prompt(
        plan_file,
        logbook.all_items(plan_id),
        metrics.for_prompt(plan_file),
        storage.list_proposals(plan_id)[:RECENT_PROPOSALS],
        history,
        today,
    )
    system = f"{SYSTEM_PROMPT}\n\n{memory.for_prompt()}"
    usage = Usage(input_tokens=0, output_tokens=0)
    turn = Turn(plan_id, client, today, [m.text for m in history if m.role == "user"][-USER_MESSAGES_FOR_ADAPTATION:])
    messages = [{"role": "user", "content": prompt}]
    texts, nudged = [], False
    for _ in range(MAX_CALLS):
        if texts:
            yield "\n\n"
        # The whole prefix is cached, so the calls after a tool use re-read it at a tenth of the price.
        reply = yield from generator.stream_text(
            client, model, system, messages, usage, MAX_TOKENS,
            tools=TOOLS, cache_control={"type": "ephemeral"}, output_config={"effort": "high"},
        )
        messages.append({"role": "assistant", "content": reply.content})
        texts += [block.text for block in reply.content if block.type == "text"]
        calls = [block for block in reply.content if block.type == "tool_use"]
        if not calls:
            if nudged or not any(mark in text for mark in LEAK_MARKS for text in texts):
                break
            nudged, texts = True, []
            messages.append({"role": "user", "content": LEAK_NUDGE})
            continue
        results = []
        for call in calls:
            content, failed = turn.run(call.name, call.input)
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": failed})
        messages.append({"role": "user", "content": results})
    else:
        raise generator.GenerationError("The coach did not finish answering. Please try again.")

    body = "\n\n".join(text.strip() for text in texts if text.strip())
    if not body:
        if not turn.proposal_id:
            raise generator.GenerationError("The coach returned an empty reply. Please try again.")
        body = "I've prepared the change below."
    coach = message(
        "coach",
        body,
        action="proposal_created" if turn.proposal_id else "none",
        proposal_id=turn.proposal_id,
        usage=usage,
        model=model,
        memory_updated=turn.memory_updated,
    )
    with storage.plan_lock(plan_id):  # read again: the other device may have chatted during the call
        storage.save_chat(plan_id, [*storage.load_chat(plan_id), user, coach])
    return {
        "messages": [user.model_dump(), coach.model_dump()],
        "proposal": storage.load_proposal(turn.proposal_id).model_dump() if turn.proposal_id else None,
    }


def send(plan_id: str, text: str, client=None, today=None) -> dict:
    """The reply in one piece: the stream, drained."""
    words = stream(plan_id, text, client, today)
    while True:
        try:
            next(words)
        except StopIteration as done:
            return done.value


def clear(plan_id: str) -> None:
    with storage.plan_lock(plan_id):
        lifecycle.require_active(plan_id)
        storage.archive_chat(plan_id, f"{datetime.now(timezone.utc):%Y-%m-%dT%H-%M-%S}")
