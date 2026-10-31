from app.models import PlanFile

SYSTEM_SUFFIX = """

You are revising an existing draft plan. Apply the athlete's feedback and return a complete new plan. Keep what the feedback does not touch unless the feedback makes it no longer fit.

Feedback overrides the request. Also return long_run_day, available_days and runs_per_week as they stand after the feedback: copy them from the request unless the feedback changes them, in which case return the new values and schedule to them."""


def build_user_prompt(base_prompt: str, draft: PlanFile, feedback: str) -> str:
    earlier = "\n".join(f"- {item}" for item in draft.meta.feedback_history) or "None"
    return f"""{base_prompt}

Current draft plan:
{draft.plan.model_dump_json()}

Earlier feedback, already applied:
{earlier}

New feedback to apply:
{feedback}"""
