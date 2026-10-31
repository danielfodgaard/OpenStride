import os
from datetime import date, datetime, timedelta, timezone

import anthropic
from pydantic import ValidationError

from app import storage
from app.models import (
    DAYS,
    DatedPlan,
    DatedSession,
    DatedWeek,
    Meta,
    Paces,
    PlanFile,
    SavedRequest,
    TrainingPlan,
    Usage,
)
from app.prompt import SYSTEM_PROMPT, build_user_prompt, week_starts
from app.validate import soft_warnings, validate_plan
from app.vdot import compute_paces

APP_VERSION = "1.0.0"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 32000
DISTANCE_SLUGS = {5: "5k", 10: "10k", 21.0975: "hm", 42.195: "m"}


class GenerationError(Exception):
    """A failure with a message fit to show the runner."""


def call_claude(client, model: str, user_prompt: str, usage: Usage) -> TrainingPlan:
    try:
        with client.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"format": {"type": "json_schema", "schema": anthropic.transform_schema(TrainingPlan)}},
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as error:
        raise GenerationError(f"The Claude API call failed: {error}") from error

    usage.input_tokens += message.usage.input_tokens
    usage.output_tokens += message.usage.output_tokens
    if message.stop_reason == "max_tokens":
        raise GenerationError("The plan was too long and got cut off. Try a shorter plan.")
    if message.stop_reason == "refusal":
        raise GenerationError("Claude declined to generate this plan. Try rewording the goal or notes.")

    text = "".join(block.text for block in message.content if block.type == "text")
    try:
        return TrainingPlan.model_validate_json(text)
    except ValidationError as error:
        raise GenerationError("Claude returned a plan in an unexpected format. Please try again.") from error


def add_dates(plan: TrainingPlan, starts: list[date]) -> DatedPlan:
    weeks = []
    # A plan that failed validation can have the wrong week count; extrapolate rather than crash.
    for index, week in enumerate(plan.weeks):
        start = starts[0] + timedelta(weeks=index)
        sessions = [
            DatedSession(**s.model_dump(), date=start + timedelta(days=DAYS.index(s.day)))
            for s in week.sessions
        ]
        weeks.append(DatedWeek(**week.model_dump(exclude={"sessions"}), start_date=start, sessions=sessions))
    return DatedPlan(summary=plan.summary, warnings=plan.warnings, weeks=weeks)


def generate_plan(request: SavedRequest, client=None, today: date | None = None) -> PlanFile:
    client = client or anthropic.Anthropic()
    model = os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
    today = today or date.today()
    now = datetime.now(timezone.utc)

    paces = Paces(**compute_paces(
        request.recent_race_distance_km,
        request.recent_race_time,
        request.race_distance_km,
        request.goal_time,
    ))
    starts = week_starts(today, request.race_date)
    user_prompt = build_user_prompt(request, paces, starts)
    usage = Usage(input_tokens=0, output_tokens=0)

    plan = call_claude(client, model, user_prompt, usage)
    errors = validate_plan(plan, request, paces, len(starts))
    if errors:
        problems = "\n".join(f"- {error}" for error in errors)
        plan = call_claude(client, model, f"{user_prompt}\n\nFix these problems:\n{problems}", usage)
        errors = validate_plan(plan, request, paces, len(starts))
    plan.warnings += errors + soft_warnings(plan)

    slug = DISTANCE_SLUGS.get(request.race_distance_km, f"{round(request.race_distance_km)}k")
    plan_file = PlanFile(
        meta=Meta(
            id=f"{now:%Y-%m-%dT%H-%M-%S}_{slug}",
            created_at=now.isoformat(timespec="seconds"),
            app_version=APP_VERSION,
            model=model,
            usage=usage,
        ),
        request=request,
        paces=paces,
        plan=add_dates(plan, starts),
    )
    storage.save_plan(plan_file)
    return plan_file
