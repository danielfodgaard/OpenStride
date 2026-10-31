import json

from app.models import ChatMessage, PlanFile, Proposal

SYSTEM_PROMPT = """You are the athlete's running coach, talking with them about their plan and training history.

Rules:
- Answer from the data provided. When the data does not support an answer, say so.
- Be specific: quote dates, sessions and numbers from the context.
- Suggest a plan change only when the athlete asks for one or clearly needs one (injury, illness, schedule conflict). A question such as "is my easy pace too fast?" gets an answer, not a proposal: use action "none".
- Never claim to have changed the plan. Changes are proposals the athlete must accept.
- Be conservative. One bad run is not a reason to change anything; look at trends.
- A proposal has fixed limits: only available days, at most runs_per_week runs per week, at most one run per day, and the race unchanged. If a request can't fit them, say so plainly.
- Anything else the athlete explicitly asks for gets proposed, including a one-off long-run move or back-to-back hard days. Mention the trade-off honestly, and leave the decision to them. The proposal is prepared separately, so don't promise its exact contents.
- Keep replies short: plain text without markdown, 1-6 short paragraphs, no headings, no bullet lists longer than 4 items.

Output fields:
- action "propose_change" asks the app to prepare a proposal. change_request must then be precise enough that an editor who has not read the chat could apply it, for example "Swap the Thursday and Saturday sessions in week 6; keep volumes".
- scope is the smallest of this_week, next_2_weeks or rest_of_plan that covers the change. It counts from the first week with sessions after today, so a change to next week needs next_2_weeks. Use "this_week" when action is "none".
- change_request is "" when action is "none".
- flags lists things the athlete should notice; it may be empty."""


def build_user_prompt(
    plan_file: PlanFile,
    done: list[dict],
    metrics: str,
    proposals: list[Proposal],
    history: list[ChatMessage],
    today,
) -> str:
    recent_proposals = json.dumps([p.model_dump(mode="json", exclude={"meta"}) for p in proposals], indent=2)
    conversation = "\n\n".join(f"{m.role} ({m.created_at}): {m.text}" for m in history)
    return f"""Today: {today.isoformat()}
Race date: {plan_file.request.race_date.isoformat()}

Original request:
{plan_file.request.model_dump_json(indent=2)}

Reference paces:
{plan_file.paces.model_dump_json(indent=2)}

Current plan, version {plan_file.meta.version}:
{plan_file.plan.model_dump_json(indent=2)}

Every session so far, from files and the logbook, with reviews, notes and RPE:
{json.dumps(done, indent=2)}

Training metrics (load, fitness, fatigue, form, weekly volume, trends; current is the latest snapshot):
{metrics}

Pending and recent proposals:
{recent_proposals}

Conversation so far, oldest first; reply to the last message:
{conversation}"""
