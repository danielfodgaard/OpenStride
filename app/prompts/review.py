import json

from app.models import ActivitySummary, ManualActivity, PlanFile

SYSTEM_PROMPT = """You are an experienced running coach reviewing one run against what was planned.

Judge the run against its purpose, not just the numbers: an easy run done too fast is worth flagging, and a good session is worth saying so. Use the laps for structured sessions. Take the athlete's note and RPE seriously. The reference paces are computed from a recent race and may have been adjusted in the plan, so the planned session comes first.

Keep it short and concrete, with real numbers. observations has 1 to 4 short points; flags lists signals worth watching and may be empty. Do not suggest plan changes here.

Use completion "unplanned" when no session was planned."""


def build_user_prompt(activity: ActivitySummary, plan_file: PlanFile, recent: list[ActivitySummary], manual: list[ManualActivity], metrics: str) -> str:
    session = None
    if activity.match.session_index >= 0:
        week = next(w for w in plan_file.plan.weeks if w.week_number == activity.match.week_number)
        session = week.sessions[activity.match.session_index]
    planned = session.model_dump_json(indent=2) if session else "Unplanned run"
    history = json.dumps([a.model_dump(mode="json", exclude={"km_splits"}) for a in recent], indent=2)
    logged = json.dumps([m.model_dump(mode="json") for m in manual], indent=2)
    return f"""Planned session:
{planned}

The run:
{activity.model_dump_json(indent=2)}

Athlete's reference paces:
{plan_file.paces.model_dump_json(indent=2)}

Previous 7 days of runs:
{history}

Hand-logged sessions from those 7 days (strength, cross-training, watch-less runs):
{logged}

Training metrics (load, fitness, fatigue, form, weekly volume, trends):
{metrics}"""
