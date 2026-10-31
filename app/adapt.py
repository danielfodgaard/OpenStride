"""Adaptation proposals. The plan changes only through `accept` and `restore`, both user actions."""
import os
from datetime import datetime, timezone

from app import activities, clock, generator, lifecycle, logbook, metrics, storage
from app.models import (
    AdaptationResult,
    HistoryEntry,
    PlanFile,
    Proposal,
    ReviewMeta,
    TrainingPlan,
    Usage,
)
from app.prompts.adapt import SYSTEM_PROMPT, build_user_prompt
from app.validate import scope_errors, validate_merge

DEFAULT_MODEL = "claude-sonnet-5-5"


def compute_scope_weeks(plan_file: PlanFile, scope: str, today) -> list[int]:
    """From the first week with a session after today, through the end of the scope."""
    numbers = [week.week_number for week in plan_file.plan.weeks]
    first = next(
        (week.week_number for week in plan_file.plan.weeks if any(s.date > today for s in week.sessions)),
        None,
    )
    if first is None:
        raise lifecycle.Conflict("This plan has no sessions left to change.")
    remaining = numbers[numbers.index(first):]
    return remaining[:{"this_week": 1, "next_2_weeks": 2}.get(scope, len(remaining))]


def merge(plan_file: PlanFile, weeks) -> PlanFile:
    """A copy of the plan with `weeks` swapped in by week number, re-dated from each week's start."""
    merged = plan_file.model_copy(deep=True)
    by_number = {week.week_number: week for week in weeks}
    merged.plan.weeks = [
        generator.date_week(by_number[week.week_number], week.start_date) if week.week_number in by_number else week
        for week in merged.plan.weeks
    ]
    return merged


def check(plan_file: PlanFile, result: AdaptationResult, scope: list[int], today) -> list[str]:
    errors = scope_errors(result, scope)
    if errors or result.recommendation == "no_change":
        return errors
    return validate_merge(plan_file, merge(plan_file, result.weeks), scope, today)


def create_proposal(plan_id: str, scope: str, user_context: str, client=None, today=None) -> Proposal:
    plan_file = lifecycle.require_active(plan_id)
    today = today or clock.today()
    weeks = compute_scope_weeks(plan_file, scope, today)
    runs = logbook.all_items(plan_id)
    prompt = build_user_prompt(plan_file, runs, metrics.for_prompt(plan_file), weeks, today, user_context)
    client = client or generator.make_client()
    model = os.environ.get("ADAPT_MODEL") or DEFAULT_MODEL
    usage = Usage(input_tokens=0, output_tokens=0)

    result = generator.call_claude(client, model, SYSTEM_PROMPT, prompt, AdaptationResult, usage)
    errors = check(plan_file, result, weeks, today)
    if errors:
        problems = "\n".join(f"- {error}" for error in errors)
        retry = f"{prompt}\n\nFix these problems:\n{problems}"
        result = generator.call_claude(client, model, SYSTEM_PROMPT, retry, AdaptationResult, usage)
        errors = check(plan_file, result, weeks, today)
    if errors:
        result.rationale += "\n\nThis proposal can't be accepted: " + " ".join(errors)

    now = datetime.now(timezone.utc)
    proposal = Proposal(
        proposal_id=storage.new_proposal_id(plan_id, f"{now:%Y-%m-%dT%H-%M-%S}_{scope}"),
        plan_id=plan_id,
        based_on_version=plan_file.meta.version,
        scope=scope,
        scope_weeks=weeks,
        user_context=user_context,
        status="pending",
        acceptable=not errors,
        result=result,
        meta=ReviewMeta(model=model, created_at=now.isoformat(timespec="seconds"), usage=usage),
    )
    storage.save_proposal(proposal)
    return proposal


def replace_plan(new: PlanFile) -> None:
    lifecycle.write_version(new)
    storage.save_plan(new)
    activities.rematch(new)
    metrics.refresh(new)


def accept(proposal_id: str, today=None) -> PlanFile:
    proposal = storage.load_proposal(proposal_id)
    plan_file = lifecycle.require_active(proposal.plan_id)
    if proposal.status != "pending":
        raise lifecycle.Conflict(f"This proposal is already {proposal.status}.")
    if not proposal.acceptable or proposal.result.recommendation != "change":
        raise lifecycle.Conflict("This proposal has nothing that can be accepted.")
    if proposal.based_on_version != plan_file.meta.version:
        raise lifecycle.Conflict("The plan has changed since this proposal. Ask for new suggestions.")
    # Days pass between proposing and accepting, so a session it moves may be in the past by now.
    errors = check(plan_file, proposal.result, proposal.scope_weeks, today or clock.today())
    if errors:
        raise lifecycle.Conflict("This proposal is out of date: " + " ".join(errors))

    merged = merge(plan_file, proposal.result.weeks)
    merged.meta.version += 1
    merged.meta.history.append(HistoryEntry(
        version=merged.meta.version,
        proposal_id=proposal.proposal_id,
        accepted_at=lifecycle.now_iso(),
        rationale=proposal.result.rationale,
    ))
    replace_plan(merged)
    proposal.status = "accepted"
    storage.save_proposal(proposal)
    return merged


def reject(proposal_id: str) -> Proposal:
    proposal = storage.load_proposal(proposal_id)
    lifecycle.require_active(proposal.plan_id)
    if proposal.status != "pending":
        raise lifecycle.Conflict(f"This proposal is already {proposal.status}.")
    proposal.status = "rejected"
    storage.save_proposal(proposal)
    return proposal


def restore(plan_id: str, version: int) -> PlanFile:
    """A new version with an old version's content. History only ever grows."""
    current = lifecycle.require_active(plan_id)
    new = storage.load_version(plan_id, version)
    new.meta.status = "active"
    new.meta.version = current.meta.version + 1
    new.meta.accepted_at = current.meta.accepted_at
    new.meta.history = [*current.meta.history, HistoryEntry(
        version=new.meta.version,
        proposal_id="",
        accepted_at=lifecycle.now_iso(),
        rationale=f"Restored version {version}",
    )]
    replace_plan(new)
    return new
