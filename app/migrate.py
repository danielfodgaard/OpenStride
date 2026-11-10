"""Bring a pre-0.2 data folder into the workspace. Nothing in the old folder is changed or removed.

The plan and its versions become plan/ and its git history. The coach's notes are carried over. Reviews,
the athlete's notes on runs, hand-logged sessions and the chat are kept as text under journal/legacy/,
where the agent can read them. Activities themselves come from the provider: with --upload, the old raw
files and hand-logged sessions are sent there first.
"""
import json
from pathlib import Path

from app import plan, providers, workspace
from app.models import DatedPlan, Paces, PlanDoc, SavedRequest
from app.providers import ProviderError


def read(path: Path):
    return json.loads(path.read_text())


def as_doc(legacy: dict) -> PlanDoc:
    """A legacy plan file as the three plan files, each session given an id in order."""
    weeks, number = [], 0
    for week in legacy["plan"]["weeks"]:
        sessions = []
        for session in week["sessions"]:
            number += 1
            sessions.append(session | {"id": session.get("id") or f"s{number}"})
        weeks.append(week | {"sessions": sessions})
    plan_fields = {"summary": legacy["plan"].get("summary", ""), "warnings": legacy["plan"].get("warnings", []), "weeks": weeks}
    return PlanDoc(request=SavedRequest.model_validate(legacy["request"]), paces=Paces.model_validate(legacy["paces"]), plan=DatedPlan.model_validate(plan_fields))


def find_plan(source: Path) -> Path | None:
    folders = sorted((source / "plans").glob("*/plan.json"), reverse=True)
    active = [p for p in folders if read(p).get("meta", {}).get("status") == "active"]
    return (active or folders or [None])[0]


def import_plan(plan_path: Path, report: list[str]) -> None:
    current = read(plan_path)
    history = {entry["version"]: entry for entry in current.get("meta", {}).get("history", [])}
    versions = sorted((plan_path.parent / "versions").glob("plan_v*.json"), key=lambda p: int(p.stem.split("_v")[1]))
    steps = [(int(p.stem.split("_v")[1]), read(p)) for p in versions]
    latest = current.get("meta", {}).get("version", 1)
    if not steps or steps[-1][0] != latest:
        steps.append((latest, current))
    with workspace.lock():
        for number, legacy in steps:
            plan.write(as_doc(legacy))
            entry = history.get(number, {})
            reason = entry.get("summary") or entry.get("rationale") or ("accepted" if number == 1 else "")
            workspace.commit_protected(f"Plan (imported version {number}){': ' + reason.splitlines()[0][:90] if reason else ''}", ["plan"])
    report.append(f"Plan: {current['request']['goal_description']!r}, {len(steps)} version(s) imported as git history.")


def legacy_text(source: Path, plan_dir: Path | None, report: list[str]) -> None:
    out = workspace.root() / "journal" / "legacy"
    reviews = []
    for path in sorted((source / "activities").glob("*.json")):
        if path.name.endswith(".series.json"):
            continue
        activity = read(path)
        review_path = source / "reviews" / path.name
        review = read(review_path)["review"] if review_path.exists() else None
        lines = [f"## {activity['date']}: {activity.get('sport', 'run')}, {activity['distance_km']:g} km, {activity.get('avg_pace', '')}, avg HR {activity.get('avg_hr') or 'n/a'}"]
        if activity.get("user_note") or activity.get("rpe"):
            lines.append(f"Athlete: {activity.get('user_note') or ''} (RPE {activity.get('rpe') or 'not given'})")
        if review:
            lines.append(f"Review ({review['completion']}, effort {review['effort']}): {review['headline']}")
            lines += [f"- {item}" for item in review.get("observations", [])]
            lines += [f"- Flag: {item}" for item in review.get("flags", [])]
        reviews.append("\n".join(lines))
    if reviews:
        workspace.write_file(out / "reviews.md", "# Runs and their reviews, from before the workspace\n\n" + "\n\n".join(reviews) + "\n")
        report.append(f"Reviews: {len(reviews)} run(s) with their reviews and notes kept in journal/legacy/reviews.md.")

    manual = sorted([*(source / "plans").glob("*/manual_activities/*.json"), *(source / "manual_activities").glob("*.json")])
    if manual:
        rows = []
        for path in manual:
            entry = read(path)
            details = "; ".join(f"{d['description']} {d['repeat']}x{d['reps']} {d['load']}".strip() for d in entry.get("details", []))
            rows.append(f"- {entry['date']}: {entry['modality']}, {entry.get('title') or ''}, {entry['duration_min']} min, RPE {entry.get('rpe') or 'n/a'}. {entry.get('note') or ''} {details}".rstrip())
        workspace.write_file(out / "logbook.md", "# Sessions logged by hand, from before the workspace\n\n" + "\n".join(rows) + "\n")
        report.append(f"Logbook: {len(manual)} hand-logged session(s) kept in journal/legacy/logbook.md.")

    if plan_dir:
        messages = [m for path in sorted(plan_dir.glob("chat*.json")) for m in read(path)]
        if messages:
            messages.sort(key=lambda m: m.get("created_at", ""))
            body = "\n\n".join(f"**{'Athlete' if m['role'] == 'user' else 'Coach'}** ({m.get('created_at', '')[:16]}): {m['text']}" for m in messages)
            workspace.write_file(out / "chat.md", "# The conversation with the coach, from before the workspace\n\n" + body + "\n")
            report.append(f"Chat: {len(messages)} message(s) kept in journal/legacy/chat.md.")


def upload_history(source: Path, report: list[str]) -> None:
    provider = providers.active()
    if provider is None:
        report.append("Upload skipped: no provider is connected.")
        return
    sent = failed = 0
    if provider.capabilities.upload:
        for path in sorted((source / "activities" / "raw").glob("*")):
            try:
                provider.upload(path.name, path.read_bytes())
                sent += 1
            except ProviderError as error:
                failed += 1
                report.append(f"  {path.name} was not uploaded: {error}")
    logged = 0
    if provider.capabilities.manual:
        for path in sorted([*(source / "plans").glob("*/manual_activities/*.json"), *(source / "manual_activities").glob("*.json")]):
            entry = read(path)
            sport = {"strength": "strength", "run": "run"}.get(entry["modality"], "other")
            try:
                provider.log_manual({"date": entry["date"], "sport": sport, "minutes": entry["duration_min"], "distance_km": entry.get("distance_km") or None,
                                     "rpe": entry.get("rpe") or None, "title": entry.get("title") or "", "note": entry.get("note") or ""})
                logged += 1
            except ProviderError as error:
                report.append(f"  {entry['date']} {entry['modality']} was not logged: {error}")
    report.append(f"Upload: {sent} recording(s) sent to {provider.name}, {failed} failed, {logged} hand-logged session(s) logged there.")


def run(source: Path, upload: bool = False, force: bool = False) -> list[str]:
    """Import what `source` holds. Returns what was done, line by line."""
    report: list[str] = []
    plan_path = find_plan(source)
    if plan_path is None and not (source / "athlete" / "memory.md").exists():
        return [f"Nothing to migrate: {source} has no plans/ and no athlete/memory.md from an earlier version."]
    if plan_path is not None:
        if plan.load() is not None and not force:
            report.append("Plan: the workspace already has one; kept it (--force replaces it).")
        else:
            import_plan(plan_path, report)
    notes = source / "athlete" / "memory.md"
    target = workspace.root() / "athlete" / "memory.md"
    if notes.exists() and (not target.exists() or target.read_text() == workspace.MEMORY_TEMPLATE or force):
        workspace.write_file(target, notes.read_text())
        report.append("Notes: the coach's notes about you carried over to athlete/memory.md.")
    legacy_text(source, plan_path.parent if plan_path else None, report)
    with workspace.lock():
        workspace.commit("Imported from the earlier version of OpenStride")
    if upload:
        upload_history(source, report)
    report.append(f"The old files in {source} were left exactly as they were.")
    if providers.active() is None:
        report.append("Next: connect a provider (INTERVALS_ICU_API_KEY in .env), and your activities arrive with the first sync.")
    return report


def auto(source: Path) -> list[str]:
    """At the first start after an upgrade: bring the old data over once, without being asked. Reads only."""
    marker = workspace.state() / "migrated"
    if marker.exists():
        return []
    has_old = find_plan(source) is not None or (source / "athlete" / "memory.md").exists()
    report = run(source) if has_old and plan.load() is None else []
    workspace.write_file(marker, workspace.now_iso())
    return report
