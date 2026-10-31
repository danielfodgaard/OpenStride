"""Coach chat. It can only ask for a proposal; the plan changes through the v2 accept flow."""
import os
import uuid
from datetime import datetime, timezone

from app import adapt, clock, generator, lifecycle, logbook, metrics, storage
from app.models import ChatMessage, CoachReply, Usage
from app.prompts.chat import SYSTEM_PROMPT, build_user_prompt

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 4000
HISTORY_LIMIT = 30
RECENT_PROPOSALS = 5
USER_MESSAGES_FOR_ADAPTATION = 3


def check_reply(reply: CoachReply) -> list[str]:
    if reply.action == "propose_change" and not reply.change_request.strip():
        return ["change_request must say exactly what to change when action is propose_change"]
    return []


def message(role: str, text: str, **fields) -> ChatMessage:
    return ChatMessage(
        id=uuid.uuid4().hex,
        role=role,
        created_at=lifecycle.now_iso(),
        text=text,
        **fields,
    )


def run_proposal(plan_id: str, reply: CoachReply, thread: list[ChatMessage], client, today):
    """Returns the text to add to the reply and the proposal id, if a proposal came of it."""
    said = [m.text for m in thread if m.role == "user"][-USER_MESSAGES_FOR_ADAPTATION:]
    context = reply.change_request + "\n\nFrom chat:\n" + "\n".join(said)
    try:
        proposal = adapt.create_proposal(plan_id, reply.scope, context, client, today, origin="chat")
    except (generator.GenerationError, lifecycle.Conflict) as error:
        return f"I couldn't prepare that change: {error}", ""
    if proposal.result.recommendation == "no_change":
        return f"Working through the details, I don't recommend a change. {proposal.result.rationale}", ""
    return "", proposal.proposal_id


def send(plan_id: str, text: str, client=None, today=None) -> dict:
    """Both messages are saved together after the reply, so a failed call leaves the thread as it was."""
    plan_file = lifecycle.require_active(plan_id)
    today = today or clock.today()
    client = client or generator.make_client()
    model = os.environ.get("CHAT_MODEL") or DEFAULT_MODEL
    thread = storage.load_chat(plan_id)
    user = message("user", text.strip())
    prompt = build_user_prompt(
        plan_file,
        logbook.all_items(plan_id),
        metrics.for_prompt(plan_file),
        storage.list_proposals(plan_id)[:RECENT_PROPOSALS],
        [*thread, user][-HISTORY_LIMIT:],
        today,
    )
    usage = Usage(input_tokens=0, output_tokens=0)
    call = lambda content: generator.call_claude(
        client, model, SYSTEM_PROMPT, content, CoachReply, usage, max_tokens=MAX_TOKENS, stream=False
    )
    reply = call(prompt)
    errors = check_reply(reply)
    if errors:
        reply = call(f"{prompt}\n\nFix these problems:\n" + "\n".join(f"- {e}" for e in errors))
        if check_reply(reply):
            reply.action = "none"

    body, proposal_id = reply.reply, ""
    if reply.action == "propose_change":
        extra, proposal_id = run_proposal(plan_id, reply, [*thread, user], client, today)
        body = f"{body}\n\n{extra}" if extra else body
    coach = message(
        "coach",
        body,
        action="proposal_created" if proposal_id else "none",
        proposal_id=proposal_id,
        usage=usage,
        model=model,
        flags=reply.flags,
    )
    with storage.plan_lock(plan_id):  # read again: the other device may have chatted during the call
        storage.save_chat(plan_id, [*storage.load_chat(plan_id), user, coach])
    return {
        "messages": [user.model_dump(), coach.model_dump()],
        "proposal": storage.load_proposal(proposal_id).model_dump() if proposal_id else None,
    }


def clear(plan_id: str) -> None:
    with storage.plan_lock(plan_id):
        lifecycle.require_active(plan_id)
        storage.archive_chat(plan_id, f"{datetime.now(timezone.utc):%Y-%m-%dT%H-%M-%S}")
