from app.models import PlanFile

SYSTEM_SUFFIX = """

You are revising an existing draft plan. Apply the athlete's feedback and return a complete new plan. Keep what the feedback does not touch unless the feedback makes it no longer fit."""


def build_user_prompt(base_prompt: str, draft: PlanFile, feedback: str) -> str:
    earlier = "\n".join(f"- {item}" for item in draft.meta.feedback_history) or "None"
    return f"""{base_prompt}

Current draft plan:
{draft.plan.model_dump_json()}

Earlier feedback, already applied:
{earlier}

New feedback to apply:
{feedback}"""
