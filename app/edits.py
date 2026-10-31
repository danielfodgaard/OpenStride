"""Changes the athlete makes by hand. Their decision, so code applies them at once: no LLM, no cost, and an undo.

Sessions are addressed by week and index, and an edit can shift those, so matches and the Garmin
mapping follow the session itself. Every edit is a new plan version, like an accepted proposal.
"""
from datetime import date, timedelta

from app import activities, clock, lifecycle, metrics, storage
from app.adapt import replace_plan
from app.garmin.delivery import key_of, session_at
from app.models import HistoryEntry, Match, PlanFile
from app.validate import merge_warnings

ASSIGN_DAYS = 6
NO_TWO_RUNS = "already has a run. Use Swap instead."


def refuse(text: str) -> None:
    raise lifecycle.Conflict(text)


def label(session) -> str:
    return f"{session.day} {session.title.lower()}"


def week_for(plan_file: PlanFile, day: date):
    return next((w for w in plan_file.plan.weeks if w.start_date <= day <= w.start_date + timedelta(days=6)), None)


def locate(plan_file: PlanFile) -> dict:
    """Each session object's week and index."""
    return {id(s): (w.week_number, i) for w in plan_file.plan.weeks for i, s in enumerate(w.sessions)}


def plan_entries(plan_id: str) -> list:
    return [
        *((a, storage.save_activity) for a in storage.list_activities(plan_id)),
        *((m, storage.save_manual) for m in storage.list_manual(plan_id)),
    ]


def run_clash(plan_file: PlanFile, session, day: date, others=()) -> bool:
    ignored = [id(session), *map(id, others)]
    return session.modality == "run" and any(
        s.date == day and s.modality == "run" and s.status != "skipped" and id(s) not in ignored
        for w in plan_file.plan.weeks for s in w.sessions
    )


def place(plan_file: PlanFile, session, day: date, note: str = "") -> None:
    """The session goes to `day`, and to the week that contains it."""
    old_week = week_for(plan_file, session.date)
    new_week = week_for(plan_file, day)
    if new_week is None:
        refuse("That date isn't in this plan.")
    original = session.moved_from or session.date
    old_day = session.day
    session.date, session.day = day, day.strftime("%a")
    session.moved_from = None if original == day else original
    session.edit_note = f"Moved from {old_day}: {note}" if note else f"Moved from {old_day}"
    if old_week is not new_week:
        old_week.sessions = [s for s in old_week.sessions if s is not session]
        new_week.sessions.append(session)
        if session.modality == "run":
            old_week.planned_km = round(old_week.planned_km - session.distance_km, 2)
            new_week.planned_km = round(new_week.planned_km + session.distance_km, 2)


def check_free(session, done: set, keys: dict) -> None:
    if session.type == "race":
        refuse("The race can't be changed by hand.")
    if keys[id(session)] in done:
        refuse("This session is already done.")


def move(plan_file, session, to_date, today, race_date, note, done, keys):
    check_free(session, done, keys)
    if to_date is None:
        refuse("Pick a date to move it to.")
    if to_date < today:
        refuse("A session can't move into the past.")
    if to_date >= race_date:
        refuse("A session can't move to the race day or after it.")
    if run_clash(plan_file, session, to_date):
        refuse(f"{to_date:%a} {NO_TWO_RUNS}")
    text = f"Moved {label(session)} to {to_date:%a}"
    place(plan_file, session, to_date, note)
    return text


def swap(plan_file, session, other, today, note, done, keys):
    check_free(session, done, keys)
    check_free(other, done, keys)
    if session is other:
        refuse("Pick a different session to swap with.")
    if session.date < today or other.date < today:
        refuse("Only sessions dated today or later can be swapped.")
    if abs(keys[id(session)][0] - keys[id(other)][0]) > 1:
        refuse("Only sessions in the same or adjacent weeks can be swapped.")
    if run_clash(plan_file, session, other.date, [other]) or run_clash(plan_file, other, session.date, [session]):
        refuse("That swap would put two runs on one day.")
    text = f"Swapped {label(session)} with {label(other)}"
    first, second = session.date, other.date
    place(plan_file, session, second, note)
    place(plan_file, other, first, note)
    return text


def skip(session, note, done, keys):
    check_free(session, done, keys)
    session.status = "skipped"
    session.edit_note = f"Skipped: {note}" if note else "Skipped"
    return f"Skipped {label(session)}"


def unskip(plan_file, session):
    if session.status != "skipped":
        refuse("This session isn't skipped.")
    if run_clash(plan_file, session, session.date):
        refuse(f"{session.day} {NO_TWO_RUNS}")
    session.status = "planned"
    session.edit_note = ""
    return f"Put {label(session)} back"


def load_entry(activity_id: str, plan_id: str):
    try:
        entry, save = storage.load_activity(activity_id), storage.save_activity
    except storage.NotFound:
        entry, save = storage.load_manual(activity_id), storage.save_manual
        if entry.plan_id != plan_id:
            refuse("Log this session against the plan first.")
    if entry.plan_id not in (plan_id, ""):
        refuse("This workout belongs to another plan.")
    return entry, save


def assign(plan_file, session, entry, note, done, keys, warnings):
    """Allowed onto a day that has another run: the run happened, so refusing would only lose a fact."""
    if session.type == "race":
        refuse("The race can't be changed by hand.")
    if entry.match.session_index >= 0:
        refuse("This workout already counts as a planned session.")
    if keys[id(session)] in done:
        refuse("This session is already done.")
    if session.modality != entry.modality:
        refuse(f"A {entry.modality} workout can only count as a planned {entry.modality} session.")
    if abs((session.date - entry.date).days) > ASSIGN_DAYS:
        refuse(f"Pick a session within {ASSIGN_DAYS} days of this workout.")
    if run_clash(plan_file, session, entry.date):
        warnings.append(f"{entry.date:%a} now has two runs. Move or skip the other one if it's no longer planned.")
    text = f"Counted {entry.date:%a} {entry.title.lower() if hasattr(entry, 'title') else 'run'} as {label(session)}"
    if session.date != entry.date:
        place(plan_file, session, entry.date, note)
    session.status = "planned"
    return text


def replace(session, entry, note, done, keys):
    # shortcut: same modality only, so a ride can't replace a run; lift it with a modality-free match.
    if session.type == "race":
        refuse("The race can't be changed by hand.")
    if session.date != entry.date:
        refuse("Pick a session planned on the day of this workout.")
    if session.modality != entry.modality:
        refuse(f"A {entry.modality} workout can only replace a planned {entry.modality} session.")
    if keys[id(session)] in done and keys[id(session)] != (entry.match.week_number, entry.match.session_index):
        refuse("Another workout already counts as this session.")
    session.status = "replaced"
    session.edit_note = f"Replaced: {note}" if note else "Replaced"
    return f"Replaced {label(session)} with what you did"


def rematch(new: PlanFile, where: dict, moved_sessions: list, entries: list, target=None) -> dict:
    """Matches follow their session; a moved session picks up a free workout on its new day."""
    after = locate(new)
    before = {}

    def set_match(entry, save, session):
        week, index = after[id(session)]
        match = Match(planned_date=session.date.isoformat(), week_number=week, session_index=index)
        if match != entry.match:
            before[entry.id] = entry.match
            entry.match = match
            save(entry)

    for entry, save in entries:
        session = where.get((entry.match.week_number, entry.match.session_index))
        if session is not None:
            set_match(entry, save, session)
    if target:
        set_match(*target)
    taken = {(e.match.week_number, e.match.session_index) for e, _ in entries}
    for session in moved_sessions:
        if after[id(session)] not in taken:
            free = next(((e, s) for e, s in entries if e.match.session_index < 0 and e.date == session.date and e.modality == session.modality), None)
            if free:
                set_match(*free, session)
                taken.add(after[id(session)])
    return before


def day_warnings(new: PlanFile, sessions: list) -> list[str]:
    allowed = new.request.available_days
    return [f"{s.day} isn't one of your usual training days" for s in sessions if s.day not in allowed]


def apply(plan_id: str, kind: str, session: dict | None = None, to_date: date | None = None, other: dict | None = None,
          activity_id: str = "", note: str = "", today: date | None = None) -> dict:
    today = today or clock.today()
    with storage.workout_lock(plan_id):
        plan_file = lifecycle.require_active(plan_id)
        new = plan_file.model_copy(deep=True)
        where = {(w.week_number, i): s for w in new.plan.weeks for i, s in enumerate(w.sessions)}
        keys = locate(new)
        entries = plan_entries(plan_id)
        done = {(e.match.week_number, e.match.session_index) for e, _ in entries if e.match.session_index >= 0}
        chosen = session_at(new, session["week"], session["index"]) if session else None
        partner = session_at(new, other["week"], other["index"]) if other else None
        warnings = []
        target = None
        note = note.strip()
        moved = []

        if chosen is None:
            refuse("Pick a session.")
        if kind == "move":
            summary = move(new, chosen, to_date, today, new.request.race_date, note, done, keys)
            moved = [chosen]
        elif kind == "swap":
            if partner is None:
                refuse("Pick a session to swap with.")
            summary = swap(new, chosen, partner, today, note, done, keys)
            moved = [chosen, partner]
        elif kind == "skip":
            summary = skip(chosen, note, done, keys)
        elif kind == "unskip":
            summary = unskip(new, chosen)
        else:
            entry, save = load_entry(activity_id, plan_id)
            if kind == "assign_activity":
                summary = assign(new, chosen, entry, note, done, keys, warnings)
                moved = []
            else:
                summary = replace(chosen, entry, note, done, keys)
            entry.plan_id = plan_id  # an unattached workout joins the plan it matches
            target = (entry, save, chosen)

        for week in new.plan.weeks:
            week.sessions.sort(key=lambda s: s.date)
        after = locate(new)
        changes = {key_of(*keys[id(s)]): key_of(*after[id(s)]) for s in where.values() if keys[id(s)] != after[id(s)]}

        new.meta.version += 1
        undo_matches = rematch(new, where, moved, entries, target)
        new.meta.history.append(HistoryEntry(
            version=new.meta.version,
            proposal_id="",
            accepted_at=lifecycle.now_iso(),
            rationale=f"{summary}: {note}" if note else summary,
            origin="manual",
            summary=summary,
            undo_matches=undo_matches,
            undo_keys={to: start for start, to in changes.items()},
        ))
        lifecycle.write_version(new)
        storage.save_plan(new)
        metrics.refresh(new)
        warnings += merge_warnings(plan_file, new) + day_warnings(new, moved)
        return {
            "plan": new,
            "version": new.meta.version,
            "summary": summary,
            "warnings": warnings,
            "garmin": {"moved": changes, "removed": [key_of(*after[id(chosen)])] if kind == "skip" else []},
        }


def undo(plan_id: str) -> dict:
    with storage.workout_lock(plan_id):
        current = lifecycle.require_active(plan_id)
        last = current.meta.history[-1] if current.meta.history else None
        if last is None or last.origin != "manual":
            refuse("There's no hand edit to undo.")
        new = storage.load_version(plan_id, last.version - 1)
        new.meta.status = "active"
        new.meta.version = current.meta.version + 1
        new.meta.accepted_at = current.meta.accepted_at
        new.meta.history = [*current.meta.history, HistoryEntry(
            version=new.meta.version,
            proposal_id="",
            accepted_at=lifecycle.now_iso(),
            rationale=f"Undid: {last.summary}",
            origin="undo",
            summary=f"Undid: {last.summary}",
        )]
        replace_plan(new)
        for entry, save in plan_entries(plan_id):
            if entry.id in last.undo_matches:
                entry.match = last.undo_matches[entry.id]
                save(entry)
        metrics.refresh(new)
        return {"plan": new, "garmin": {"moved": last.undo_keys, "removed": []}}
