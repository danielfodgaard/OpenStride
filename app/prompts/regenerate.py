from app.models import PlanFile

SYSTEM_SUFFIX = """

You are revising an existing draft plan. Apply the athlete's feedback and return a complete new plan. Keep what the feedback does not touch unless the feedback makes it no longer fit.

The request's schedule fields (long_run_day, available_days, runs_per_week) are checked against the plan. If the feedback conflicts with one of them, keep to the request and add a warning telling the athlete to change that field in the form and regenerate."""


def build_user_prompt(base_prompt: str, draft: PlanFile, feedback: str) -> str:
    earlier = "\n".join(f"- {item}" for item in draft.meta.feedback_history) or "None"
    return f"""{base_prompt}

Current draft plan:
{draft.plan.model_dump_json()}

Earlier feedback, already applied:
{earlier}

New feedback to apply:
{feedback}"""
