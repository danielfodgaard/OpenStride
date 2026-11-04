"""Changes the athlete makes by hand. Their decision, so code applies them at once: no LLM, no cost, and an undo.

Sessions are addressed by week and index, and an edit can shift those, so matches and the Garmin
mapping follow the session itself. Every edit is a new plan version, like an accepted proposal.
"""
from datetime import date

from app import clock, lifecycle, metrics, storage
from app.activities import plan_entries
from app.adapt import revive
from app.garmin.delivery import key_of, session_at
from app.models import DatedSession, HistoryEntry, Match, PlanFile
from app.validate import MODALITIES, merge_warnings

ASSIGN_DAYS = 6
NO_TWO_RUNS = "already has a run. Use Swap instead."
KINDS = {"run": "run", "strength": "strength session", "cross": "cross-training session"}
ADDED_TYPE = {"run": "easy", "strength": "strength", "cross": "cross_training"}
ADDED_TITLE = {"run": "Run", "strength": "Strength", "cross": "Cross-training"}


def refuse(text: str) -> None:
    raise lifecycle.Conflict(text)


def label(session) -> str:
    return f"{session.day} {session.title.lower()}"


def locate(plan_file: PlanFile) -> dict:
    """Each session object's week and index."""
    return {id(s): (w.week_number, i) for w in plan_file.plan.weeks for i, s in enumerate(w.sessions)}


def run_clash(plan_file: PlanFile, session, day: date, others=()) -> bool:
    ignored = [id(session), *map(id, others)]
    return session.modality == "run" and any(
        s.date == day and s.modality == "run" and s.status != "skipped" and id(s) not in ignored
        for w in plan_file.plan.weeks for s in w.sessions
    )


def place(plan_file: PlanFile, session, day: date, note: str = "") -> None:
    """The session goes to `day`, and to the week that contains it."""
    old_week = plan_file.week_of(session.date)
    new_week = plan_file.week_of(day)
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


def change(session, changes: dict, today, note, done, keys):
    """The athlete rewrites what the session is. Steps go when the amount or pace changes: they no longer describe it."""
    check_free(session, done, keys)
    if session.date < today:
        refuse("Only sessions dated today or later can be changed.")
    changes = {k: v for k, v in changes.items() if v is not None and v != getattr(session, k)}
    if not changes:
        refuse("Nothing changed.")
    if "type" in changes and MODALITIES[changes["type"]] != session.modality:
        refuse(f"A {session.modality} session can't become a {changes['type']} session. Skip it and add the other kind instead.")
    was = label(session)
    diffs = []
    if "distance_km" in changes:
        diffs.append(f"{session.distance_km:g} to {changes['distance_km']:g} km")
    if "duration_min" in changes:
        diffs.append(f"{session.duration_min:g} to {changes['duration_min']:g} min")
    if "target_pace" in changes:
        diffs.append(f"{session.target_pace or 'no pace'} to {changes['target_pace'] or 'no pace'}")
    if "type" in changes:
        diffs.append(f"{session.type} to {changes['type']}")
    if changes.keys() & {"distance_km", "duration_min", "target_pace"}:
        session.steps = []
    for key, value in changes.items():
        setattr(session, key, value)
    what = ", ".join(diffs) or "the wording"
    session.edit_note = f"Changed {what}: {note}" if note else f"Changed {what}"
    return f"Changed {was}: {what}"


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
    check_free(session, done, keys)
    if entry.match.session_index >= 0:
        refuse("This workout already counts as a planned session.")
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
    if session.modality != entry.modality:
        refuse(f"A {entry.modality} workout can only replace a planned {entry.modality} session.")
    if keys[id(session)] in done and keys[id(session)] != (entry.match.week_number, entry.match.session_index):
        refuse("Another workout already counts as this session.")
    session.status = "replaced"
    session.edit_note = f"Replaced: {note}" if note else "Replaced"
    return f"Replaced {label(session)} with what you did"


def unreplace(session):
    if session.status != "replaced":
        refuse("This session wasn't replaced.")
    session.status = "planned"
    session.edit_note = ""
    return f"{label(session).capitalize()} counts as planned again"


def what(entry) -> str:
    """The workout, as the summary names it: a logged session by its title, a file by its kind."""
    title = getattr(entry, "title", "").strip()
    return title.lower() if title else KINDS[entry.modality]


def release(entry, where: dict, entries: list) -> None:
    """The session this workout counted as goes back to planned, unless another workout still counts as it."""
    key = (entry.match.week_number, entry.match.session_index)
    old = where.get(key)
    others = any(e.id != entry.id and (e.match.week_number, e.match.session_index) == key for e, _ in entries)
    if old is not None and old.status == "replaced" and not others:
        old.status = "planned"
        old.edit_note = ""


def count(session, entry, done, keys, warnings):
    """The workout counts as this session, whatever the two are dated: the plan day shows what the athlete did."""
    if session.modality != entry.modality:
        refuse(f"A {KINDS[entry.modality]} can only count as a planned {KINDS[entry.modality]}.")
    if keys[id(session)] == (entry.match.week_number, entry.match.session_index):
        refuse("This workout already counts as that session.")
    if keys[id(session)] in done:
        warnings.append(f"Another workout also counts as {label(session)}.")
    if session.status == "skipped":
        session.status = "planned"
        session.edit_note = ""
    return f"Counted {entry.date:%a} {what(entry)} as {label(session)}"


def add(plan_file: PlanFile, entry, day: date) -> DatedSession:
    """A day with nothing of this kind planned gets a session that is the workout itself."""
    week = plan_file.week_of(day)
    if week is None:
        refuse("That date isn't in this plan.")
    minutes = entry.duration_min if hasattr(entry, "duration_min") else round(entry.moving_time_s / 60)
    distance = round(entry.distance_km, 2) if entry.modality == "run" else 0
    title = getattr(entry, "title", "").strip() or ADDED_TITLE[entry.modality]
    session = DatedSession(
        day=day.strftime("%a"), type=ADDED_TYPE[entry.modality], modality=entry.modality, title=title,
        distance_km=distance, duration_min=minutes, target_pace="", steps=[], date=day,
        description=f"Added from your {KINDS[entry.modality]} on {entry.date:%a %-d %b}.",
        edit_note=f"Added from a {KINDS[entry.modality]}",
    )
    week.sessions.append(session)
    if session.modality == "run":
        week.planned_km = round(week.planned_km + distance, 2)
    return session


def rematch(new: PlanFile, where: dict, moved_sessions: list, entries: list, target=None) -> dict:
    """Matches follow their session; a moved session picks up a free workout on its new day."""
    after = locate(new)
    before = {}

    def set_match(entry, save, session):
        if session is None:  # the workout leaves the plan's sessions
            match = Match()
        else:
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
          activity_id: str = "", note: str = "", changes: dict | None = None, today: date | None = None) -> dict:
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
        added = []

        if chosen is None and kind != "match_activity":
            refuse("Pick a session.")
        if kind == "match_activity":
            entry, save = load_entry(activity_id, plan_id)
            release(entry, where, entries)
            if chosen is not None:
                summary = count(chosen, entry, done, keys, warnings)
            elif to_date is not None:
                chosen = add(new, entry, to_date)
                added = [chosen]
                summary = f"Added {entry.date:%a} {what(entry)} to the plan on {to_date:%a}"
            elif entry.match.session_index < 0:
                refuse("This workout isn't in the plan.")
            else:
                summary = f"Took {entry.date:%a} {what(entry)} out of the plan"
            if chosen is not None:
                entry.plan_id = plan_id  # an unattached workout joins the plan it matches
            target = (entry, save, chosen)
        elif kind == "move":
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
        elif kind == "unreplace":
            summary = unreplace(chosen)
        elif kind == "change":
            before_km = chosen.distance_km
            summary = change(chosen, changes or {}, today, note, done, keys)
            if chosen.modality == "run":
                week = new.week_of(chosen.date)
                week.planned_km = round(week.planned_km - before_km + chosen.distance_km, 2)
        else:
            entry, save = load_entry(activity_id, plan_id)
            if kind == "assign_activity":
                summary = assign(new, chosen, entry, note, done, keys, warnings)
            else:
                summary = replace(chosen, entry, note, done, keys)
            entry.plan_id = plan_id  # an unattached workout joins the plan it matches
            target = (entry, save, chosen)

        for week in new.plan.weeks:
            week.sessions.sort(key=lambda s: s.date)
        after = locate(new)
        changes = {key_of(*keys[id(s)]): key_of(*after[id(s)]) for s in where.values() if keys[id(s)] != after[id(s)]}
        if kind == "change":
            changes[key_of(*after[id(chosen)])] = key_of(*after[id(chosen)])  # same key, so Garmin re-checks its content

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
        warnings += merge_warnings(plan_file, new) + day_warnings(new, [*moved, *added])
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
        new = revive(current, last.version - 1, rationale=f"Undid: {last.summary}", origin="undo", summary=f"Undid: {last.summary}")
        for entry, save in plan_entries(plan_id):
            if entry.id in last.undo_matches:
                entry.match = last.undo_matches[entry.id]
                save(entry)
        metrics.refresh(new)
        return {"plan": new, "garmin": {"moved": last.undo_keys, "removed": []}}
