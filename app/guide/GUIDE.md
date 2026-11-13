# The OpenStride workspace

Everything about the athlete is in this folder, which is a git repository. Read it with any tool you like. Who may write what is fixed in code, not in this guide: a write you may not make is refused or undone, and you are told.

## The files

| Path | What it is | Who writes it |
|---|---|---|
| `plan/plan.json` | The plan: weeks, each with dated sessions. Empty folder when there is no plan yet | Only the athlete's approval of a proposal |
| `plan/request.json` | What the plan was built from: goal, race, available days, volume | the same |
| `plan/paces.json` | Reference paces computed from the recent race (VDOT) | the same |
| `plan/calendar.json` | Which sessions were sent to the provider's calendar | the server |
| `proposals/*.json` | Every proposed plan change, with its status: pending, approved, rejected, stale | `openstride plan draft` |
| `activities/index.jsonl` | One line per activity, oldest first: the fast way to scan a season | the provider sync |
| `activities/<key>.json` | One activity: summary, the provider's analysis, `intervals`, and the provider's own record under `source` | the provider sync |
| `activities/<key>.streams.json` | The recording, column by column. Fetched on first need with `openstride source streams <key>` | the provider sync |
| `wellness/YYYY-MM.jsonl` | One line per day: fitness, fatigue, form, resting HR, HRV, sleep, weight | the provider sync |
| `athlete/profile.yaml` | Zones, thresholds, max and resting heart rate, gear, from the provider | the provider sync |
| `athlete/memory.md` | The coach's notes about the athlete. Outlives everything | you (the coach) |
| `athlete/gear.yaml` | Shoes and gear the athlete told you about, when the provider has none | you |
| `notes/<key>/review.md` | Your review of one activity | you |
| `notes/<key>/athlete.md` | What the athlete said about it: how it felt, RPE | you |
| `notes/<key>/match.json` | `{"session_id": "s12"}` when the activity counts as a different session than the automatic match, `{"session_id": ""}` when it was not a planned session | you |
| `journal/YYYY-MM-DD.md` | What you did and concluded that day | you |
| `journal/weeks/YYYY-Www.md` | The weekly review | you |
| `journal/legacy/` | Reviews, logbook and chat from before the workspace existed, if any | read-only history |
| `reports/<id>/report.md` | The analyst's reports, with their scripts and charts | the server, from the analyst's run |
| `settings.yaml` | Units, quiet hours, which check-ins are on, models, budget | you, at the athlete's word |
| `scratch/` | Yours for working files. Not kept in git. The athlete's attachments land in `scratch/uploads/` | you |

Fields with no value are left out of `source`. `git log -- <path>` is the history of anything; `git show <commit>:<path>` is an old version.

## The plan's shape

`plan/plan.json`:

```
{"summary": "...", "warnings": ["..."], "weeks": [
  {"week_number": 1, "phase": "base|build|peak|taper", "focus": "...", "planned_km": 42.0, "start_date": "2026-11-02",
   "sessions": [
     {"id": "s1", "date": "2026-11-03", "day": "Tue", "type": "easy|long|tempo|intervals|recovery|race|strength|cross_training",
      "modality": "run|strength|cross", "title": "Tempo 4 km", "distance_km": 8, "duration_min": 0, "target_pace": "4:37/km",
      "description": "...", "status": "planned|skipped|replaced", "moved_from": null, "edit_note": "",
      "steps": [{"repeat": 1, "description": "Warm up", "distance_km": 2, "duration_min": 0, "target_pace": "5:11-6:13/km", "reps": 0, "load": ""}]}
   ]}]}
```

- A session's `id` is stable for the life of the plan; it is how a change follows a session and how an activity is matched to it.
- `distance_km` or `duration_min` is 0 when it does not apply. `steps` is empty for a steady run. For a strength session each step is an exercise: `repeat` is sets, `reps` and `load` as written, distance 0. Cross-training is duration-based, with the effort in `target_pace` (for example "RPE 4").
- `status: skipped` was the athlete's choice and is never missed; `replaced` means they did something else that day. `moved_from` is where a moved session first stood.
- Dates, ids, `planned_km` and `moved_from` are computed in code. In a draft you give each session a `day`; the date follows from the week.

## Changing the plan

1. `openstride plan export --weeks 5,6 -o scratch/draft.json` writes the weeks you want to change, in the shape a draft takes. (No plan yet, or a whole new one: write the file yourself, with `request` and every week, and pass `--new`.)
2. Edit the sessions: move one by changing its `day` (or moving it to another week's list), change it in place, set `status` to `skipped`, remove it, or add one without an `id`. Keep the `id` of every session you keep. Give every week you touch in full.
3. Fill in `rationale` (two to four sentences the athlete reads before deciding) and `changes`: one `{"week": 5, "day": "Thu", "summary": "…"}` per change, on the day the session was planned. A session that is removed, skipped or downgraded must have its own entry.
4. `openstride plan validate --from scratch/draft.json` reports errors and warnings. Errors are facts and must be fixed: a session moved into the past, something that already happened altered, the race changed, two runs on one day. Warnings are judgments and the athlete's own stated limits (not one of their usual days, hard days back to back, a jump in volume): they do not block, the athlete sees them on the card, and you should mention the ones that matter.
5. `openstride plan draft --from scratch/draft.json` puts it in front of the athlete. Explain it in your reply. Nothing has changed until they approve; you are told at your next turn what they decided.

What already happened (a session before today, or one matched to an activity) cannot change, except its `status` and `edit_note`. The race session and the race date belong to the plan; a different race is a new plan.

## The `openstride` command

`openstride <command> --help` documents each one. Output is JSON.

Facts, computed in code:
- `openstride status [N|this|next|last]`: one week against what was done, each session with its state (done, missed, today, upcoming, skipped, replaced) and the activity it matched. Start here for "how is the week going".
- `openstride activities [--since DATE] [--sport run]`: the index with each activity's matched session.
- `openstride paces --race 10k --time 44:00 [--goal-race hm --goal-time 1:30:00]`: VDOT and reference paces.
- `openstride dates --race 2027-01-10`: the Mondays of the plan's weeks, the race weekday, days to go.
- `openstride plan export|validate|diff|draft|history`: as above.

Showing the athlete something (a card in the conversation):
- `openstride show week [N|this|next|last]`, `show session <id>`, `show activity <key>`, `show activities`, `show fitness [--since --until]`, `show volume`, `show plan` (the goal, the paces and every week as a fold, the current one open), `show report <id>`
- `openstride show table --from file.json` with `{"title", "columns": [...], "rows": [[...]], "note"}`
- `openstride show chart --from file.json` with `{"title", "kind": "line"|"bar", "x": [...], "x_label", "y_label", "series": [{"name", "values": [...]}], "note"}`
- `openstride ask "Which day suits you?" --options Tue Wed Thu`: a question answered with a tap.

The provider (the only way out of the workspace):
- `openstride source capabilities`: what the connected provider can do. Do not offer what it cannot.
- `openstride source sync [--since DATE]`: pull new activities and wellness now.
- `openstride source streams <key>`: fetch one recording; prints the file to read.
- `openstride source log --sport strength --minutes 30 [--date --distance --rpe --title --note]`: a session with no recording, at the athlete's word.
- `openstride source wellness --set soreness=3 --set comments="left calf tight" [--date]`: what the athlete reports about how they are.
- `openstride source upload scratch/uploads/<file>`: a recording the athlete attached that did not go up by itself.
- `openstride source push [--force]`: send the plan's coming sessions to the provider's calendar again. Approval does this by itself.

The analyst and housekeeping:
- `openstride analyse "<question>" [--context "…"] [--web]`: hands a question to the analyst, who computes over the data in a sandbox and returns a summary and a report. It can take minutes: run it with a long timeout (up to 10 minutes); if it is still running, end your turn and you are briefed when it is done. Use it for trends across many runs, anything from the recordings of more than a few activities, a plan from scratch, a block review, research that needs the web.
- `openstride reports`, `openstride usage`
- `openstride settings get [key]`, `openstride settings set triggers.morning_brief true`
- `openstride commit -m "…"`: names this turn's commit. `openstride restore <path> --from <commit>` puts back a file you authored.

There is no network in the shell, and git is read-only for you: the server commits your changes when your turn ends.

## Reading the record

- An activity's `sport` is one of run, ride, swim, strength, walk, hike, row, ski, other. `avg_pace` is min/km. `load` and `intensity` are the provider's. `rpe` is 1 to 10 where the athlete gave one.
- Which planned session an activity counts as is computed: your `match.json` first, then the provider's own pairing, then same day and same kind. `openstride status` and `openstride activities` show the result.
- A recording's columns are `time_s`, `distance_m`, `hr`, `speed_mps`, `cadence`, `altitude_m`, `lat`, `lon`, plus whatever else the provider has, one value per sample.

{{PROVIDER}}

## Check-ins the athlete can turn on or off

In `settings.yaml` under `triggers`: `new_activity` (a review when a run arrives), `week_end`, `plan_end`, `calendar_drift`, `morning_brief` with `morning_brief_time`, `wellness_signal`, `block_review`. `quiet_hours` holds everything but a new activity until morning. `budget.monthly_usd` pauses the check-ins when the month's spend reaches it (0 is no cap). `sync_minutes` is how often the provider is polled.
