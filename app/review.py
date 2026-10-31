"""LLM review of one run. Reviews never change the plan."""
import os
from datetime import timedelta

from app import generator, lifecycle, metrics, storage
from app.models import ActivitySummary, ReviewFile, ReviewMeta, RunReview, Usage
from app.prompts.review import SYSTEM_PROMPT, build_user_prompt

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 8000


def recent_activities(activity: ActivitySummary) -> list[ActivitySummary]:
    start = activity.date - timedelta(days=7)
    return [a for a in storage.list_activities(activity.plan_id) if start <= a.date and a.start_time < activity.start_time]


def review_activity(activity: ActivitySummary, client=None) -> ReviewFile:
    plan_file = lifecycle.require_active(activity.plan_id)
    model = os.environ.get("REVIEW_MODEL") or DEFAULT_MODEL
    usage = Usage(input_tokens=0, output_tokens=0)
    review = generator.call_claude(
        client or generator.make_client(),
        model,
        SYSTEM_PROMPT,
        build_user_prompt(activity, plan_file, recent_activities(activity), metrics.for_prompt(plan_file)),
        RunReview,
        usage,
        max_tokens=MAX_TOKENS,
        stream=False,
    )
    review_file = ReviewFile(
        activity_id=activity.id,
        review=review,
        meta=ReviewMeta(model=model, created_at=lifecycle.now_iso(), usage=usage),
    )
    storage.save_review(review_file)
    return review_file
