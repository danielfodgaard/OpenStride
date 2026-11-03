"""Planned-vs-actual arithmetic, and what the plan has cost so far."""
from datetime import date

from app import activities, clock, lifecycle, metrics, storage
from app.models import PlanFile, Proposal
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
    for week, volume in zip(plan_file.plan.weeks, metrics.build_weeks(done, plan_file)):
        here = [entry for entry in done if metrics.in_week(entry, week)]
        row = {
            "week_number": week.week_number,
            "start_date": week.start_date.isoformat(),
            "planned_km": volume.planned_km,
            "actual_km": volume.actual_km,
            "sessions_planned": volume.planned_sessions,
            "sessions_completed": volume.completed_sessions,
            "sessions_skipped": volume.skipped_sessions,
            "sessions_replaced": volume.replaced_sessions,
        } | {completion: 0 for completion in COMPLETIONS}
        for entry in here:
            review_file = storage.load_review(entry.id)
            if review_file:
                row[review_file.review.completion] += 1
        weeks.append(row)
    keys = ("planned_km", "actual_km", "sessions_planned", "sessions_completed", "sessions_skipped", "sessions_replaced", *COMPLETIONS)
    totals = {key: round(sum(row[key] for row in weeks), 2) for key in keys}
    return {"weeks": weeks, "totals": totals}


def session_ref(week, index: int, session, done: dict) -> dict:
    entry = done.get((week.week_number, index))
    return session.model_dump(mode="json", include={"title", "type", "date", "distance_km", "duration_min", "target_pace"}) | {
        "week_number": week.week_number,
        "index": index,
        "done": entry is not None,
        "workout_id": entry.id if entry else "",
        "actual_km": round(entry.km, 2) if entry else 0,
        "actual_pace": activities.format_pace(entry.minutes * 60, entry.km) if entry and entry.km else "",
        "actual_min": round(entry.minutes) if entry else 0,
        "actual_hr": entry.avg_hr if entry else 0,
    }


def plan_status(plan_file: PlanFile, today: date | None = None) -> dict:
    """The overview's facts: where the plan stands today and what's next."""
    today = today or clock.today()
    weeks = plan_file.plan.weeks
    race = plan_file.request.race_date
    current = plan_file.week_of(today)
    entries = metrics.entries(plan_file.meta.id)
    done = {(e.match.week_number, e.match.session_index): e for e in reversed(entries) if e.match.session_index >= 0}
    # To date, plus anything already done: test data and a wrong watch clock can date a run ahead of today.
    planned = [
        (w, i, s) for w in weeks for i, s in enumerate(w.sessions)
        if s.status != "skipped" and (s.date <= today or (w.week_number, i) in done)
    ]
    completed = lambda modality: sum(
        (w.week_number, i) in done for w, i, s in planned if s.modality == modality
    )
    count = lambda modality: sum(s.modality == modality for _, _, s in planned)

    upcoming = sorted(
        ((w, i, s) for w in weeks for i, s in enumerate(w.sessions) if s.date >= today and s.status != "skipped"),
        key=lambda item: (item[2].date, item[2].modality != "run"),
    )
    is_done = lambda item: (item[0].week_number, item[1]) in done
    next_session = next((item for item in upcoming if not is_done(item)), None)
    today_done = next((item for item in upcoming if item[2].date == today and is_done(item)), None)
    return {
        "today": today.isoformat(),
        "week_number": current.week_number if current else 0,
        "weeks_total": len(weeks),
        "phase": current.phase if current else "",
        "started": weeks[0].start_date <= today,
        "finished": race < today,
        "days_to_race": (race - today).days,
        "km_planned": round(sum(s.distance_km for _, _, s in planned if s.modality == "run"), 1),
        "km_actual": round(sum(e.km for e in entries if e.modality == "run"), 1),
        "sessions_planned": len(planned),
        "sessions_completed": sum((w.week_number, i) in done for w, i, _ in planned),
        "strength_planned": count("strength"),
        "strength_completed": completed("strength"),
        "cross_planned": count("cross"),
        "cross_completed": completed("cross"),
        "today_done": session_ref(*today_done, done) if today_done else None,
        "next": session_ref(*next_session, done) if next_session else None,
    }


def usage_totals(plan_id: str) -> dict:
    """Every saved usage for this plan: drafts, proposals, reviews and chat."""
    plan_file = lifecycle.require_plan(plan_id)
    folder = storage.plan_dir(plan_id)
    metas = [plan_file.meta]
    metas += [PlanFile.model_validate_json(p.read_text()).meta for p in (folder / "versions").glob("draft_v*.json")]
    metas += [Proposal.model_validate_json(p.read_text()).meta for p in (folder / "proposals").glob("*.json")]
    for run in storage.list_activities(plan_id):
        review_file = storage.load_review(run.id)
        if review_file:
            metas.append(review_file.meta)
    metas += [m for m in storage.all_chat_messages(plan_id) if m.role == "coach"]

    cost = 0.0
    for meta in metas:
        input_price, output_price = PRICES.get(meta.model, (0, 0))
        usage = meta.usage
        # Cache reads cost a tenth of the input price, cache writes a quarter more.
        tokens_in = usage.input_tokens + usage.cache_read_input_tokens * 0.1 + usage.cache_creation_input_tokens * 1.25
        cost += (tokens_in * input_price + usage.output_tokens * output_price) / 1_000_000
    return {
        "models": sorted({meta.model for meta in metas}),
        "input_tokens": sum(m.usage.input_tokens + m.usage.cache_read_input_tokens + m.usage.cache_creation_input_tokens for m in metas),
        "cached_tokens": sum(m.usage.cache_read_input_tokens for m in metas),
        "output_tokens": sum(meta.usage.output_tokens for meta in metas),
        "cost_usd": round(cost, 4),
        "unpriced_models": sorted({
            meta.model for meta in metas
            if meta.model not in PRICES and meta.usage.input_tokens + meta.usage.output_tokens
        }),
    }
