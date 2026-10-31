import json

from app.models import PlanFile

SYSTEM_PROMPT = """You are an experienced running coach deciding whether a training plan needs to change.

Rules:
- Be conservative. A single bad or missed session is normally not a reason to change the plan. Look at trends over the last 1-2 weeks.
- Change the plan only for clear signals: injury or pain, illness, repeated missed or failed sessions, consistently much easier or harder than planned, or a real-life constraint the athlete mentions.
- If nothing warrants a change, return recommendation "no_change" with a rationale, and empty changes and weeks. This is a valid, good outcome.
- Otherwise return recommendation "change" and only the weeks listed in scope_weeks, complete, with every session of those weeks. Everything else stays as it is.
- Sessions dated today or earlier have already happened: copy them exactly into the week. Do not move them.
- Keep the race date and race session exactly as they are.
- Keep the plan's scheduling rules: only available days, at most runs_per_week per week, one session per day, long run on long_run_day, no two hard sessions (tempo, intervals, long) on consecutive days.
- Every change gets one Change entry whose summary gives the change and the reason in one line.
- rationale is 2-4 sentences: why change or why not. Paces may differ from the reference if the athlete context justifies it."""


def build_user_prompt(plan_file: PlanFile, activities: list[dict], scope_weeks: list[int], today, user_context: str) -> str:
    return f"""Today: {today.isoformat()}
Scope weeks: {json.dumps(scope_weeks)}

Athlete's context for this request:
{user_context or "None given"}

Original request:
{plan_file.request.model_dump_json(indent=2)}

Reference paces:
{plan_file.paces.model_dump_json(indent=2)}

Current plan, version {plan_file.meta.version}:
{plan_file.plan.model_dump_json(indent=2)}

Every run so far, with its review, the athlete's notes and RPE:
{json.dumps(activities, indent=2)}"""
