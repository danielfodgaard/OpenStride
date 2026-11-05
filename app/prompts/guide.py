"""What the athlete sees in the app, for the coach: the pages, the charts and how their numbers are computed.

Kept in step with the app by hand. Nothing here is a coaching rule; it lets the coach answer "what does this chart
mean" and "where do I do X" from the same facts the page shows.
"""
from app.metrics import ATL_DAYS, CTL_DAYS, DEFAULT_HR_MAX, DEFAULT_HR_REST, INTENSITY

FACTORS = ", ".join(f"{kind} {factor}" for kind, factor in INTENSITY.items())

APP_GUIDE = f"""The app, as the athlete sees it. Use this to explain a page or a chart, and to say where to click. Never describe something the app does not have.

Top menu: Workouts, Plans, Coach (this conversation), New plan, Coach data. A Dark/Light button. In the footer: Download backup, Garmin settings.

Plans: every plan with its status (draft, active, archived); one plan is active at a time. A plan's page shows, top to bottom: the goal, distance, goal time, race date and days to go; "This week" (the week number and phase, planned km and sessions done, today's session); "To date" (running km done against planned so far, sessions, strength, cross-training); the "Fitness & fatigue" chart; the "Weekly volume" chart; a "View weeks" link; "Talk to your coach"; "Planned vs actual" (a table per week: planned and actual km, sessions planned, done, skipped, replaced, and the review outcomes); "Plan versions" (every accepted proposal, hand edit and restore; "All versions" opens each one read-only); Download JSON. Each chart has a "Table" fold with its numbers.

The Fitness & fatigue chart: three lines per day from the plan's first day, the race marked. Each session gets a load: with heart rate, Banister TRIMP = minutes x HRR x 0.64 x e^(1.92 x HRR), where HRR = (average HR - resting HR) / (max HR - resting HR), max and resting HR from the plan request, else 208 - 0.7 x age when the age is known, else {DEFAULT_HR_MAX} and {DEFAULT_HR_REST}; without heart rate, minutes x a factor by session type ({FACTORS}; a replaced session counts as what was actually done). Fitness (CTL) is an exponentially weighted average of daily load over {CTL_DAYS} days; Fatigue (ATL) the same over {ATL_DAYS} days; Form (TSB) is yesterday's fitness minus yesterday's fatigue, so today's run does not count against today's form. Both averages start seeded at 1.1 x the weekly km the athlete reported before the plan. The line under the chart gives today's fitness, its change over 7 days, fatigue and form. The training metrics in your context are the chart's own numbers: days (load, ctl, atl, tsb, km per day), weeks (volume), current (ctl, atl, tsb, ctl_7d_change, weekly_km_4wk_avg).

The Weekly volume chart: planned km (grey) against completed km (blue) per week, the percentage done above each bar, and "S done/planned - X done/planned" (strength, cross-training) under it. A skipped session leaves the week's planned total; a future week shows planned only.

Weeks: one fold per week with its phase, focus and totals, then a row per day: date, day, type, session, planned amount, target pace, actual (the matched workout), review. The "..." menu on a session: Move to a day, Swap with another session in the same or next week, Change (distance, duration, target pace, type, title, description), Skip or Unskip, Delete from the plan (sessions still ahead). On a laptop a session can be dragged onto a day or another session. "To your watch" under a week: download a session as a .fit, or Send to Garmin when push is set up. A session's own page shows the planned session and the matched workout, if any.

Workouts: Sync from Garmin, Import runs (.fit or .gpx files), Log a session by hand (strength, cross-training or a watch-less run), and every workout newest first with its review. A workout's page: the route, pace and heart rate over distance with the planned range, laps or km splits against the targets, a summary, the planned session, the review (a chip: As planned, Partly, Missed targets or Unplanned; a headline, observations, flags and effort), a note and RPE the athlete can save, Re-run review, "In your plan" (pick any plan day for the workout; "I did something else than planned" marks the session replaced), Delete workout.

New plan: the request form (goal, race distance and date, goal time, experience, current weekly km, longest recent run, a recent race, runs per week, available days, long-run day, strength and cross-training sessions, equipment, age, max and resting HR, notes), then a draft to accept, or to regenerate with feedback. Accepting archives the previous active plan.

Coach data: your notes about the athlete, which they can read, edit and restore to an earlier version, and what the coaching has cost in tokens. Garmin settings: the Garmin login, sync and push.

Every hand edit, accepted proposal and restore is a new plan version; a hand edit can be undone from the toast right after it."""
