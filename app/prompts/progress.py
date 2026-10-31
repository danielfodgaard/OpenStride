import json

from app.models import PlanFile

SYSTEM_PROMPT = """You are an experienced running coach summarising how an athlete's training is going against their plan and goal.

Base the assessment on the planned-vs-actual table, the runs, their reviews and the athlete's notes. Be concrete and honest, with real numbers. Weigh recent trends more than single sessions.

observations are short points on what has happened. suggestions are short and for the athlete to consider; they are never applied automatically."""


def build_user_prompt(plan_file: PlanFile, table: dict, activities: list[dict], today) -> str:
    return f"""Today: {today.isoformat()}

Original request:
{plan_file.request.model_dump_json(indent=2)}

Reference paces:
{plan_file.paces.model_dump_json(indent=2)}

Current plan, version {plan_file.meta.version}:
{plan_file.plan.model_dump_json(indent=2)}

Planned vs actual:
{json.dumps(table, indent=2)}

Every run so far, with its review, the athlete's notes and RPE:
{json.dumps(activities, indent=2)}"""
