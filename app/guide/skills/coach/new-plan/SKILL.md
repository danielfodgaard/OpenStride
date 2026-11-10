---
name: new-plan
description: Set up a training plan from scratch, in conversation. Use when the athlete has no plan, wants a new one, has a new race, or the old plan has ended.
---

# A new plan

There is no form. You find out what the plan needs by talking, and you ask only for what you do not already have.

## 1. What you already know

Read `athlete/memory.md`, `athlete/profile.yaml`, the last months of `activities/index.jsonl` and, if there was one, the old `plan/request.json`. A recent race or hard effort, typical weekly volume, the longest recent run and the days they usually train are often all in the record. Say what you found and let them correct it.

## 2. What you need

The request, which becomes `plan/request.json`:

| Field | Meaning |
|---|---|
| `goal_description` | The goal in their words |
| `race_distance_km`, `race_date`, `goal_time` | The race. `goal_time` is null for "best effort". The race must be 3 to 24 weeks out |
| `recent_race_distance_km`, `recent_race_time` | A recent race or all-out effort, for the reference paces. From the record if it has one |
| `current_weekly_km`, `longest_recent_run_km` | From the last four weeks of the record |
| `runs_per_week`, `available_days`, `long_run_day` | Days are Mon..Sun. `available_days` must hold at least `runs_per_week` days and include the long-run day |
| `experience` | beginner, intermediate or advanced |
| `strength_sessions_per_week`, `equipment` | 0 to 3, and what they have to train with |
| `cross_training`, `cross_sessions_per_week` | The sport, and 0 to 3 |
| `hr_max`, `hr_rest`, `age` | From the profile where it has them; 0 when unknown |
| `notes` | Anything else that shapes the plan: terrain, injury history, travel |

Ask for what is missing a few questions at a time, the most important first: the race and the goal, then the days. Use `openstride ask` for choices. Do not ask for a number the record gives you; confirm it instead.

## 3. Facts before the plan

- `openstride dates --race <date>` gives the number of weeks and each week's Monday. The plan has exactly those weeks, numbered from 1.
- `openstride paces --race <recent> --time <time> --goal-race <race> --goal-time <goal>` gives the reference paces and both VDOTs. If the goal VDOT is more than about 2 points per 8 weeks above the current one, say so plainly now, before building anything.

Tell the athlete in two or three sentences what you are about to build and check it is what they want.

## 4. Build it

Hand the build to the analyst, with everything it needs in `--context`: the complete request as JSON, the paces, what your notes say that should shape the plan (injury history, what worked before, agreements), and anything the athlete said about how they want to train.

```
openstride analyse "Build the training plan for this request and draft it for approval (build-plan skill)." --context "<request JSON, paces, and what should shape the plan>"
```

It drafts the plan as a proposal; the athlete sees it as a card. When the analyst reports back, explain the plan in your own words: the shape of the block, the key sessions, where it is cautious and why, and any warning the validator raised. If the analyst cannot run, build the draft yourself the same way (the build-plan rules below apply to you too) and draft it with `openstride plan draft --from <file> --new`.

Coaching rules for the plan, whoever builds it: start volume near the current weekly volume and raise it at most about 10% a week with a lighter week every third or fourth; long run on the long-run day; no two hard runs (tempo, intervals, long) on consecutive days, across the week boundary too; taper one to three weeks by race distance; the last week has the race on race day and nothing after it; strength never the day before the long run or the race; cross-training replaces easy volume, not quality.

## 5. After they decide

Approved: the plan is live and on their watch calendar if the provider has one. Write the plan's start in your notes under Plan history, and ask the one or two things you most want to know for coaching it. Rejected: ask what was wrong, and go again.
