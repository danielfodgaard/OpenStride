import os
from datetime import date, datetime, timedelta, timezone

import anthropic
from pydantic import ValidationError

from app import clock, storage
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
    Week,
)
from app.prompts import regenerate as regenerate_prompt
from app.prompts.plan import SYSTEM_PROMPT, build_user_prompt
from app.validate import soft_warnings, validate_plan
from app.vdot import compute_paces

APP_VERSION = "3.0.0"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 64000
DISTANCE_SLUGS = {5: "5k", 10: "10k", 21.0975: "hm", 42.195: "m"}


class GenerationError(Exception):
    """A failure with a message fit to show the runner."""


def make_client() -> anthropic.Anthropic:
    # Only the offline demo runs without a key. The SDK would fail with an obscure TypeError.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise GenerationError("This needs an API key, and none is set.")
    return anthropic.Anthropic()


def call_claude(client, model, system, user_prompt, output_model, usage, max_tokens=MAX_TOKENS, stream=True):
    """One structured Claude call. Adds to `usage`; returns an `output_model` instance."""
    request = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"format": {"type": "json_schema", "schema": anthropic.transform_schema(output_model)}},
    )
    try:
        if stream:
            with client.messages.stream(**request) as response:
                message = response.get_final_message()
        else:
            message = client.messages.create(**request)
    except anthropic.APIError as error:
        raise GenerationError(f"The Claude API call failed: {error}") from error

    usage.input_tokens += message.usage.input_tokens
    usage.output_tokens += message.usage.output_tokens
    if message.stop_reason == "max_tokens":
        raise GenerationError("The response was too long and got cut off. Please try again.")
    if message.stop_reason == "refusal":
        raise GenerationError("Claude declined this request. Try rewording it.")

    text = "".join(block.text for block in message.content if block.type == "text")
    try:
        return output_model.model_validate_json(text)
    except ValidationError as error:
        raise GenerationError("Claude returned a response in an unexpected format. Please try again.") from error


def date_week(week: Week, start: date) -> DatedWeek:
    sessions = [
        DatedSession(**s.model_dump(), date=start + timedelta(days=DAYS.index(s.day)))
        for s in week.sessions
    ]
    return DatedWeek(**week.model_dump(exclude={"sessions"}), start_date=start, sessions=sessions)


def add_dates(plan: TrainingPlan, starts: list[date]) -> DatedPlan:
    # A plan that failed validation can have the wrong week count; extrapolate rather than crash.
    weeks = [date_week(week, starts[0] + timedelta(weeks=i)) for i, week in enumerate(plan.weeks)]
    return DatedPlan(summary=plan.summary, warnings=plan.warnings, weeks=weeks)


def build_plan(request, system, make_prompt, client, today, plan_id=None) -> PlanFile:
    """Generate, validate with one retry, and wrap in a PlanFile. `make_prompt` shapes the user prompt."""
    client = client or make_client()
    model = os.environ.get("PLAN_MODEL") or os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL  # v1 name
    today = today or clock.today()
    now = datetime.now(timezone.utc)

    starts = clock.week_starts(today, request.race_date)
    if not starts:
        raise GenerationError("The race date has passed. Change it and try again.")
    paces = Paces(**compute_paces(
        request.recent_race_distance_km,
        request.recent_race_time,
        request.race_distance_km,
        request.goal_time,
    ))
    user_prompt = make_prompt(build_user_prompt(request, paces, starts))
    usage = Usage(input_tokens=0, output_tokens=0)

    plan = call_claude(client, model, system, user_prompt, TrainingPlan, usage)
    errors = validate_plan(plan, request, len(starts))
    if errors:
        problems = "\n".join(f"- {error}" for error in errors)
        plan = call_claude(client, model, system, f"{user_prompt}\n\nFix these problems:\n{problems}", TrainingPlan, usage)
        errors = validate_plan(plan, request, len(starts))
    # A regenerated plan can repeat warnings it saw in the draft.
    plan.warnings += [w for w in errors + soft_warnings(plan) if w not in plan.warnings]

    slug = DISTANCE_SLUGS.get(request.race_distance_km, f"{round(request.race_distance_km)}k")
    return PlanFile(
        meta=Meta(
            id=plan_id or storage.new_plan_id(f"{now:%Y-%m-%dT%H-%M-%S}_{slug}"),
            created_at=now.isoformat(timespec="seconds"),
            app_version=APP_VERSION,
            model=model,
            usage=usage,
        ),
        request=request,
        paces=paces,
        plan=add_dates(plan, starts),
    )


def generate_plan(request: SavedRequest, client=None, today: date | None = None) -> PlanFile:
    plan_file = build_plan(request, SYSTEM_PROMPT, lambda prompt: prompt, client, today)
    storage.save_plan(plan_file)
    return plan_file


def regenerate_plan(draft: PlanFile, feedback: str, request: SavedRequest, client=None, today=None) -> PlanFile:
    """A replacement for `draft`, not yet saved; the caller keeps the old one first."""
    return build_plan(
        request,
        SYSTEM_PROMPT + regenerate_prompt.SYSTEM_SUFFIX,
        lambda prompt: regenerate_prompt.build_user_prompt(prompt, draft, feedback),
        client,
        today,
        plan_id=draft.meta.id,
    )
