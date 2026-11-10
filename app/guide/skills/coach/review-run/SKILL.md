---
name: review-run
description: Review a new activity against what was planned. Use on a new_activity brief, when the athlete uploads or mentions a run they just did, or asks how a run went.
---

# Reviewing a run

## Read

- The activity: `activities/<key>.json`. Its `intervals` are the laps or the work and recovery blocks; `source` has the provider's full analysis.
- What was planned: `openstride status` for the week shows which session it matched. If the match is wrong (a tempo matched to the easy run of the same day, a run that was not a plan session), correct it with `notes/<key>/match.json`.
- The context: the last week of activities, the wellness line for that day and the days before, your notes about the athlete, and anything they said about it.
- The recording, only if the review turns on how the run unfolded (pace fading, heart-rate drift, whether the reps were even): `openstride source streams <key>`, and read it with code, not by eye.

An activity with `partial: true` has no data to review. Say so, say why, and say how to fix it.

## Judge

Judge the run against its purpose, not only its numbers. An easy run done too fast is worth saying; so is a session done well. For a structured session use the intervals against the planned steps. Take the athlete's own words and RPE seriously. The planned session comes before the reference paces, which are only a starting point.

One run is one data point. Flag what is worth watching (heart rate high for the pace, a fade, a niggle they mentioned); do not propose a plan change from one run unless they are hurt or ill.

## Write

- `notes/<key>/review.md`: a headline, one to four observations with real numbers, and flags if any. Short.
- `notes/<key>/athlete.md` when they told you how it felt.
- A line in today's journal.
- Your notes about the athlete, if this run taught you something lasting about how they respond.

## Say

Two to four sentences for the athlete: did it do its job, the one number that shows it, and anything to watch. Show the activity card (`openstride show activity <key>`) when the shape of the run is the point. If something that matters is unknown, ask one question, such as how it felt, if they gave no RPE. For an unremarkable easy run, one or two sentences is right.

For a strength or cross-training session there is little to analyse: note that it is done and whether it replaced what was planned.
