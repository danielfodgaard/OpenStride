---
name: build-plan
description: Build a training plan from a request and draft it for the athlete's approval. Use when asked to build or rebuild a plan.
---

# Building a plan

The context gives you the request, the paces and what should shape the plan. Read the workspace too: the last months of `activities/index.jsonl` (what volume and intensity they actually handle), `wellness/`, and the old plan's git history if there was one.

## The frame, from code

- `openstride dates --race <race_date>`: the number of weeks and each week's Monday. Exactly that many weeks, numbered from 1.
- `openstride paces --race <recent km> --time <recent time> --goal-race <race km> --goal-time <goal>`: reference paces. They are a starting point; adjust them if the record justifies it, and say so in the plan's summary.

## The draft file

```
{
 "rationale": "Two to four sentences for the athlete: the shape of the plan and why.",
 "request": { ...the complete request... },
 "summary": "The plan in a short paragraph: phases, peak week, taper, where it is cautious.",
 "warnings": ["Anything the athlete should know, e.g. an ambitious goal"],
 "weeks": [
  {"week_number": 1, "phase": "base", "focus": "Settle into four runs",
   "sessions": [
    {"day": "Tue", "type": "easy", "title": "Easy 8 km", "distance_km": 8, "target_pace": "5:11-6:13/km", "description": "Relaxed, conversational."},
    {"day": "Thu", "type": "intervals", "title": "5 x 800 m", "distance_km": 9, "target_pace": "4:09/km", "description": "Even reps; the jog is easy.",
     "steps": [
      {"description": "Warm up", "distance_km": 2, "target_pace": "5:11-6:13/km"},
      {"repeat": 5, "description": "Repetition", "distance_km": 0.8, "target_pace": "4:09/km"},
      {"repeat": 4, "description": "Jog recovery", "distance_km": 0.4, "target_pace": "5:11-6:13/km"},
      {"description": "Cool down", "distance_km": 2, "target_pace": "5:11-6:13/km"}]},
    {"day": "Thu", "type": "strength", "title": "Strength A", "duration_min": 30, "description": "After the run.",
     "steps": [{"repeat": 3, "description": "Split squat", "reps": 8, "load": "2 x 10 kg"}]},
    {"day": "Sun", "type": "long", "title": "Long run 16 km", "distance_km": 16, "target_pace": "5:11-6:13/km", "description": "Easy throughout."}
   ]}
 ]
}
```

Write it with a script, not by hand, so the arithmetic of weekly volume and progression is computed. Sessions need no `id`, `date` or `modality`; `planned_km` is added up for you. Paces are written `m:ss/km` or `m:ss-m:ss/km`, so the watch can take them. Reps and their recoveries are consecutive steps with `repeat` set on both. Keep descriptions short.

## The rules

- Only on `available_days`; at most `runs_per_week` runs, `strength_sessions_per_week` strength and `cross_sessions_per_week` cross-training sessions a week; at most one run a day (strength or cross may share a day with a run).
- Start weekly volume near what the record shows they run now. Raise it at most about 10% a week, with a lighter week every third or fourth. Taper the last one to three weeks by race distance.
- The long run is on `long_run_day`. No two hard runs (tempo, intervals, long) on consecutive days, Sunday to Monday included.
- The last week has exactly one `race` session, on the race date's weekday even if that is not an available day, and nothing after it.
- Strength: 20 to 40 minutes, 4 to 8 exercises, only with their equipment, never the day before the long run or the race. Cross-training replaces easy volume, never quality.
- Let what the coach's notes say shape it: an injury history, what worked, what was agreed. If the request conflicts with them (a volume that hurt them before), follow the request and add a warning.

## Check and draft

`openstride plan validate --from <file> --new` until there are no errors. Read the warnings: fix the ones that are your mistakes, keep the ones that are honest trade-offs. Then `openstride plan draft --from <file> --new`. It goes to the athlete as a card; say in your summary to the coach what the plan is and what you were unsure of.

For a rebuild of the rest of a running plan (not a new race), export the remaining weeks with `openstride plan export --weeks <n,…>`, change them, and draft without `--new`: sessions already done stay as they are, and every session you remove or downgrade needs an entry in `changes`.
