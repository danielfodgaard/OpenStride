"""Planned-vs-actual arithmetic, an on-demand LLM summary, and what the plan has cost so far."""
import os
from datetime import date, datetime, timedelta, timezone

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


def session_ref(week, index: int, session, done: dict) -> dict:
    entry = done.get((week.week_number, index))
    return {
        "week_number": week.week_number,
        "index": index,
        "title": session.title,
        "type": session.type,
        "date": session.date.isoformat(),
        "distance_km": session.distance_km,
        "duration_min": session.duration_min,
        "target_pace": session.target_pace,
        "done": entry is not None,
        "workout_id": entry.id if entry else "",
        "actual_km": round(entry.km, 2) if entry else 0,
        "actual_min": round(entry.minutes) if entry else 0,
        "actual_hr": entry.avg_hr if entry else 0,
    }


def plan_status(plan_file: PlanFile, today: date | None = None) -> dict:
    """The overview's facts: where the plan stands today and what's next."""
    today = today or clock.today()
    weeks = plan_file.plan.weeks
    race = plan_file.request.race_date
    current = next((w for w in weeks if w.start_date <= today <= w.start_date + timedelta(days=6)), None)
    done_entries = [e for e in metrics.entries(plan_file.meta.id) if e.date <= today]
    done = {(e.match.week_number, e.match.session_index): e for e in reversed(done_entries) if e.match.session_index >= 0}
    planned = [(w, i, s) for w in weeks for i, s in enumerate(w.sessions) if s.date <= today]
    completed = lambda modality: sum(
        (w.week_number, i) in done for w, i, s in planned if s.modality == modality
    )
    count = lambda modality: sum(s.modality == modality for _, _, s in planned)

    upcoming = sorted(
        ((w, i, s) for w in weeks for i, s in enumerate(w.sessions) if s.date >= today),
        key=lambda item: (item[2].date, item[2].modality != "run"),
    )
    todays = [item for item in upcoming if item[2].date == today]
    # A session still to do today comes first; a day that's all done shows as done.
    undone = [item for item in todays if (item[0].week_number, item[1]) not in done]
    later = [item for item in upcoming if item[2].date > today]
    chosen = (undone or todays or later or [None])[0]
    return {
        "today": today.isoformat(),
        "week_number": current.week_number if current else 0,
        "weeks_total": len(weeks),
        "phase": current.phase if current else "",
        "started": weeks[0].start_date <= today,
        "finished": race < today,
        "days_to_race": (race - today).days,
        "km_planned": round(sum(s.distance_km for _, _, s in planned if s.modality == "run"), 1),
        "km_actual": round(sum(e.km for e in done_entries if e.modality == "run"), 1),
        "sessions_planned": len(planned),
        "sessions_completed": sum((w.week_number, i) in done for w, i, _ in planned),
        "strength_planned": count("strength"),
        "strength_completed": completed("strength"),
        "cross_planned": count("cross"),
        "cross_completed": completed("cross"),
        "next": session_ref(chosen[0], chosen[1], chosen[2], done) if chosen else None,
    }


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
    """Every saved usage for this plan: drafts, proposals, reviews, summaries and chat."""
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

    metas += [m for m in storage.all_chat_messages(plan_id) if m.role == "coach"]

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
