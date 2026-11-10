"""The demo's stand-in for the agent: it reads the message for a word it knows and answers from the cards."""
import re

from app import cards, clock, ops, plan
from app.runtime.scripted import Scripted


def last_line(prompt: str) -> str:
    return next((line for line in reversed(prompt.splitlines()) if line.strip()), "").lower()


def show(card: str, **args):
    command = f"openstride show {card} {' '.join(str(v) for v in args.values())}".strip()
    return ("tool", "Bash", {"command": command}, lambda: ops.show("coach", card, args))


def move_next_easy():
    """A real proposal: the next easy run, moved to the first later day of its week that has no run."""
    doc, today = plan.load(), clock.today()
    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    for session in sorted(doc.plan.sessions(), key=lambda s: s.date):
        if session.date <= today or session.type != "easy" or session.status != "planned":
            continue
        week = doc.plan.week_of(session.date)
        taken = {s.day for s in week.sessions if s.modality == "run"}
        free = [day for day in days[days.index(session.day) + 1:] if day not in taken]
        if not free:
            continue
        draft = plan.as_draft(doc, f"Moves {session.title} from {session.day} to {free[0]}, as you asked. Nothing else changes.", [week.week_number])
        for item in draft["weeks"][0]["sessions"]:
            if item["id"] == session.id:
                item["day"] = free[0]
                item["edit_note"] = "Moved at your request"
        draft["changes"] = [{"week": week.week_number, "day": session.day, "summary": f"{session.title} moves to {free[0]}"}]
        return draft, session, free[0]
    return None, None, None


def script(request):
    text = last_line(request.prompt)
    if request.prompt.find("[brief:") >= 0:
        yield ("say", "SILENT")
        return
    if re.search(r"move|swap|shift|change|skip", text):
        draft, session, day = move_next_easy()
        if draft is None:
            yield ("say", "There is no easy run ahead with a free day after it in this demo.")
            return
        try:
            result = ops.draft("coach", draft)
        except ops.OpError as error:
            yield ("say", f"I tried to draft that, and the validator refused it: {error} {' '.join(error.problems)}")
            return
        yield ("tool", "Bash", {"command": "openstride plan draft --from scratch/draft.json"}, None)
        warning = f" One thing to weigh: {result['warnings'][0]}." if result["warnings"] else ""
        yield ("say", f"I've drafted moving **{session.title}** to {day}. Nothing has changed yet: the card above shows the difference, and the plan only changes if you approve it.{warning}")
    elif "next week" in text:
        yield show("week", week="next")
        yield ("say", "Here is next week as the plan has it.")
    elif re.search(r"\bweek\b|today|tomorrow", text):
        yield show("week", week="this")
        view = cards.status("this")
        yield ("say", f"Week {view['week_number']} of {view['of']}, {view['phase']} phase: **{view['done_km']:g} of {view['planned_km']:g} km** so far, {view['sessions_done']} of {view['sessions_planned']} sessions done.")
    elif re.search(r"fitness|form|fatigue|fresh|tired", text):
        yield show("fitness")
        latest = cards.fitness_card()["latest"]
        yield ("say", f"Fitness is **{latest['fitness']:g}**, fatigue {latest['fatigue']:g}, so form is {latest['form']:g}. Positive form means you are fresh; a hard block usually sits a little below zero.")
    elif re.search(r"volume|mileage|kilomet", text):
        yield show("volume")
        yield ("say", "Planned against done, week by week.")
    elif re.search(r"last run|latest|recent|activity|workout|run", text):
        rows = [row for row in cards.activities_card(30)["activities"] if row["sport"] == "run"]
        if rows:
            yield show("activity", key=rows[0]["key"])
            yield ("say", f"Your last run: **{rows[0]['distance_km']:g} km at {rows[0]['avg_pace']}**, average heart rate {rows[0]['avg_hr']}.")
        else:
            yield ("say", "There are no runs in the demo data yet.")
    elif re.search(r"plan|race|goal|pace", text):
        yield show("plan")
        view = cards.plan_card()
        yield ("say", f"**{view['goal']}**. {view['days_to_go']} days to the race on {view['race_date']}.")
    else:
        yield ("say", "In the demo I answer to a few words: **this week**, **next week**, **the plan**, **fitness**, **volume**, **my last run**, and **move my next easy run**. With an API key and a provider connected, this box takes anything.")


def demo_runtime() -> Scripted:
    return Scripted(script)
