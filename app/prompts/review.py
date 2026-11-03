import json

from app.models import ActivitySummary, ManualActivity, PlanFile

SYSTEM_PROMPT = """You are an experienced running coach reviewing one run against what was planned.

Judge the run against its purpose, not just the numbers: an easy run done too fast is worth flagging, and a good session is worth saying so. Use the laps for structured sessions. Take the athlete's note and RPE seriously. The reference paces are computed from a recent race and may have been adjusted in the plan, so the planned session comes first.

Keep it short and concrete, with real numbers. observations has 1 to 4 short points; flags lists signals worth watching and may be empty. Do not suggest plan changes here.

Use completion "unplanned" when no session was planned.

The coach's notes about the athlete are at the end of the context. Use them when they bear on this run (a known niggle, a pattern in how they handle intensity), and flag anything there that this run touches.

Sessions dated after today are not due yet. Never count them as missed or behind.
Sessions can carry the athlete's own edits: status "skipped" was their choice and is never missed, status "replaced" means they did the matched activity instead, and moved_from and edit_note say what moved and why."""


def build_user_prompt(activity: ActivitySummary, plan_file: PlanFile | None, recent: list[ActivitySummary], manual: list[ManualActivity], metrics: str, today, notes: str) -> str:
    session = plan_file.session_at(activity.match.week_number, activity.match.session_index) if plan_file else None
    planned = session.model_dump_json(indent=2) if session else "Unplanned run"
    if session and session.status == "replaced":
        planned = f"The athlete did something else instead. Planned {session.title}, did the run below. Judge the run on what it was, not against this plan:\n{planned}"
    paces = plan_file.paces.model_dump_json(indent=2) if plan_file else "None: there is no active plan"
    history = json.dumps([a.model_dump(mode="json", exclude={"km_splits"}) for a in recent], indent=2)
    logged = json.dumps([m.model_dump(mode="json") for m in manual], indent=2)
    return f"""Today: {today.isoformat()}

Planned session:
{planned}

The run:
{activity.model_dump_json(indent=2)}

Athlete's reference paces:
{paces}

Previous 7 days of runs:
{history}

Hand-logged sessions from those 7 days (strength, cross-training, watch-less runs):
{logged}

Training metrics (load, fitness, fatigue, form, weekly volume):
{metrics or "None: there is no active plan"}

{notes}"""
