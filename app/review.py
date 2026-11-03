"""LLM review of one run. Reviews never change the plan."""
import os
from datetime import timedelta

from app import clock, generator, lifecycle, memory, metrics, storage
from app.models import ActivitySummary, ManualActivity, ReviewFile, ReviewMeta, RunReview, Usage
from app.prompts.review import SYSTEM_PROMPT, build_user_prompt

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 8000


def recent_activities(activity: ActivitySummary) -> list[ActivitySummary]:
    start = activity.date - timedelta(days=7)
    return [a for a in storage.list_activities(activity.plan_id) if start <= a.date and a.start_time < activity.start_time]


def recent_manual(activity: ActivitySummary) -> list[ManualActivity]:
    start = activity.date - timedelta(days=7)
    return [m for m in storage.list_manual(activity.plan_id) if start <= m.date <= activity.date]


def review_activity(activity: ActivitySummary, client=None) -> ReviewFile:
    plan_file = lifecycle.require_active(activity.plan_id) if activity.plan_id else None
    model = os.environ.get("REVIEW_MODEL") or DEFAULT_MODEL
    usage = Usage(input_tokens=0, output_tokens=0)
    review = generator.call_claude(
        client or generator.make_client(),
        model,
        SYSTEM_PROMPT,
        build_user_prompt(activity, plan_file, recent_activities(activity), recent_manual(activity), metrics.for_prompt(plan_file) if plan_file else "", clock.today(), memory.for_prompt()),
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
