"""Planned-vs-actual arithmetic, an on-demand LLM summary, and what the plan has cost so far."""
import os
from datetime import datetime, timezone

from app import clock, generator, lifecycle, logbook, metrics, storage
from app.models import PlanFile, ProgressFile, ProgressSummary, Proposal, ReviewMeta, Usage
from app.prompts.progress import SYSTEM_PROMPT, build_user_prompt

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 8000
COMPLETIONS = ("as_planned", "partly", "missed_targets", "unplanned")
# Dollars per million input and output tokens.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}


def planned_vs_actual(plan_file: PlanFile) -> dict:
    done = metrics.entries(plan_file.meta.id)
    weeks = []
    for week in plan_file.plan.weeks:
        here = [entry for entry in done if metrics.in_week(entry, week)]
        row = {
            "week_number": week.week_number,
            "start_date": week.start_date.isoformat(),
            "planned_km": week.planned_km,
            "actual_km": round(sum(entry.km for entry in here if entry.modality == "run"), 2),
            "sessions_planned": len(week.sessions),
            "sessions_completed": len({entry.match.session_index for entry in here if entry.match.session_index >= 0}),
        } | {completion: 0 for completion in COMPLETIONS}
        for entry in here:
            review_file = storage.load_review(entry.id)
            if review_file:
                row[review_file.review.completion] += 1
        weeks.append(row)
    keys = ("planned_km", "actual_km", "sessions_planned", "sessions_completed", *COMPLETIONS)
    totals = {key: round(sum(row[key] for row in weeks), 2) for key in keys}
    return {"weeks": weeks, "totals": totals}


def latest_summary(plan_id: str) -> ProgressFile | None:
    paths = sorted((storage.DATA_DIR / "progress").glob(f"{storage.safe_id(plan_id)}_*.json"))
    return ProgressFile.model_validate_json(paths[-1].read_text()) if paths else None


def summarise(plan_id: str, client=None, today=None) -> ProgressFile:
    plan_file = lifecycle.require_active(plan_id)
    runs = logbook.all_items(plan_id)
    model = os.environ.get("PROGRESS_MODEL") or DEFAULT_MODEL
    usage = Usage(input_tokens=0, output_tokens=0)
    prompt = build_user_prompt(plan_file, planned_vs_actual(plan_file), runs, metrics.for_prompt(plan_file), today or clock.today())
    summary = generator.call_claude(
        client or generator.make_client(),
        model,
        SYSTEM_PROMPT,
        prompt,
        ProgressSummary,
        usage,
        max_tokens=MAX_TOKENS,
        stream=False,
    )
    now = datetime.now(timezone.utc)
    progress_file = ProgressFile(
        plan_id=plan_id,
        summary=summary,
        meta=ReviewMeta(model=model, created_at=now.isoformat(timespec="seconds"), usage=usage),
    )
    path = storage.DATA_DIR / "progress" / f"{plan_id}_{now:%Y-%m-%dT%H-%M-%S}.json"
    storage.write_json(path, progress_file.model_dump_json(indent=2))
    return progress_file


def usage_totals(plan_id: str) -> dict:
    """Every saved meta.usage for this plan: drafts, proposals, reviews and summaries."""
    plan_file = lifecycle.require_plan(plan_id)
    folder = storage.plan_dir(plan_id)
    metas = [plan_file.meta]
    metas += [PlanFile.model_validate_json(p.read_text()).meta for p in (folder / "versions").glob("draft_v*.json")]
    metas += [Proposal.model_validate_json(p.read_text()).meta for p in (folder / "proposals").glob("*.json")]
    for run in storage.list_activities(plan_id):
        review_file = storage.load_review(run.id)
        if review_file:
            metas.append(review_file.meta)
    metas += [
        ProgressFile.model_validate_json(p.read_text()).meta
        for p in (storage.DATA_DIR / "progress").glob(f"{storage.safe_id(plan_id)}_*.json")
    ]

    cost = 0.0
    for meta in metas:
        input_price, output_price = PRICES.get(meta.model, (0, 0))
        cost += (meta.usage.input_tokens * input_price + meta.usage.output_tokens * output_price) / 1_000_000
    return {
        "models": sorted({meta.model for meta in metas}),
        "input_tokens": sum(meta.usage.input_tokens for meta in metas),
        "output_tokens": sum(meta.usage.output_tokens for meta in metas),
        "cost_usd": round(cost, 4),
        "unpriced_models": sorted({
            meta.model for meta in metas
            if meta.model not in PRICES and meta.usage.input_tokens + meta.usage.output_tokens
        }),
    }
