# OpenStride

Open-source running coach. Generate a training plan with strength and cross-training, import the runs you actually do, get each one reviewed, follow your fitness on charts, talk to your coach, and adapt the plan when you choose to.

## Design principle

OpenStride is a thin app on top of the LLM. The LLM makes every coaching judgment. Code only:

- parses files and does arithmetic and dates (facts),
- defines JSON schemas and renders them (structure),
- runs thin checks whose errors go back to the LLM to fix (verification), and never fixes a plan itself,
- controls when the LLM is asked and lets you approve the result (control).

The LLM gets as much context as is relevant: the full request, the plan, every run summary, review, logged session and note, and the same metrics table the charts draw from. Nothing changes the plan unless you press Accept on a proposal; the chat can only ask for one.

## What blocks a plan and what doesn't

Errors are facts and limits you set yourself. They go back to Claude to fix: sessions on available days, session counts, the proposal's scope weeks, past sessions untouched, the race fixed, and in a proposal, every removed or downgraded session having its own change entry with a reason.

Warnings are coaching judgments. They never block anything and appear above a proposal's diff so you see the trade-off before accepting: hard runs on consecutive days and volume jumps, always. The long run on `long_run_day` is an error when a plan is generated, because you asked for it in the form, and only a warning in a proposal, so a one-off move is allowed.

## Setup

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    cp .env.example .env   # then set ANTHROPIC_API_KEY

## Run

    .venv/bin/uvicorn app.main:app

Open http://127.0.0.1:8000. Data lives in `data/`.

Upgrading from v1: run `.venv/bin/python -m app.migrate` once. It moves `plans/*.json` to `data/plans/<id>/plan.json` as drafts.

## Offline demo

No API key or network needed:

    OPENSTRIDE_DEMO=1 .venv/bin/uvicorn app.main:app

This serves a throwaway copy of `examples/demo/data` with the date pinned to Wednesday 25 November 2026: an active half-marathon plan with strength and cycling, three weeks of runs with real reviews, two logged strength sessions, a cycling file, one bad run with a note, the charts, a chat thread with a pending proposal, and a progress summary. Your `data/` is not touched and every change disappears when the server stops.

Offline you can read the thread, accept or reject the proposal, view and restore versions, open the chart tables, log a session, and upload `examples/activities/cross/2026-11-12_pool_swim.fit`, which imports with a "review failed" message. Actions that need Claude show an error; with a key in `.env` they work in the demo too.

## Test

    .venv/bin/pytest

## Importing files

`.fit` and `.gpx` activities are summarised in code and matched to the planned session on the same date: running to runs, other sports to cross-training, and a watch's gym file to strength. The summary, not the raw file, is what reaches the LLM. GPX counts as running unless `<type>` says otherwise, including Strava's numeric types.

Limits to know:
- File times are UTC. A `.fit` that records its local time uses that; otherwise, as with `.gpx`, the server's time zone applies, so a run while travelling can land on the wrong day. The match can be changed by hand.
- The same run uploaded as both `.fit` and `.gpx` counts once if the start times are within 60 seconds.
- A multisport file counts as one activity of its longest sport.
- Runs dated before the plan starts are shown under "Runs outside the plan dates". Those from the 180 days before it appear in the trend charts as unplanned easy runs. Fitness and fatigue start at plan start, from the seed below, so older runs don't affect them.

## Metrics

`app/metrics.py` computes everything on the charts: load (Banister TRIMP from heart rate, or duration times a type factor without one), fitness (CTL, 42-day average), fatigue (ATL, 7-day average), form (TSB), weekly volume and pace, heart-rate and efficiency trends. Set max and resting heart rate in the form for accurate load; otherwise 208 − 0.7 × age, or 190, and 55 are used. Fitness starts from `current_weekly_km × 1.1`, a rough prior.

## How paces work

`app/vdot.py` applies the Daniels–Gilbert formulas to your recent race. Those paces are a reference: Claude may adjust them if your context justifies it, and says why in the summary.

## Models and cost

Set per task in `.env`: `PLAN_MODEL`, `ADAPT_MODEL`, `PROGRESS_MODEL`, `CHAT_MODEL` (default Claude Sonnet 5.5) and `REVIEW_MODEL` (default Claude Haiku 5.5). The v1 name `CLAUDE_MODEL` still works as a fallback for `PLAN_MODEL`.

Every saved LLM output records its token usage, and the plan page shows the running total. Measured from real calls on the sample half-marathon plan with three weeks of training logged, at list prices of $2/$10 per million tokens for Sonnet 5.5 and $0.10/$0.50 for Haiku 5.5:

| Action | Model | Tokens in | Tokens out | Cost |
|---|---|---|---|---|
| Generate a 10-week plan with strength and cycling | Haiku 5.5 | 2,868 | 23,152 | $0.012 |
| Regenerate with feedback | Haiku 5.5 | 8,634 | 14,051 | $0.008 |
| Review a run (average of 13) | Haiku 5.5 | 7,519 | 617 | $0.0011 |
| Chat message, a question | Sonnet 5.5 | 50,492 | 909 | $0.110 |
| Chat message that leads to a proposal | Sonnet 5.5 | 50,935 | 2,607 | $0.128 |
| The proposal itself | Sonnet 5.5 | 52,011 | 4,056 | $0.145 |
| The proposal, when validation forces a retry | Sonnet 5.5 | 104,300 | 11,019 | $0.319 |
| Adaptation from the form, early in the plan | Sonnet 5.5 | 20,164 | 1,691 | $0.057 |
| Progress summary | Sonnet 5.5 | 51,287 | 4,070 | $0.143 |

Plan generation has not been measured on the default Sonnet 5.5. The same token counts would cost about $0.24, but tokens differ between models. Chat, adaptation and progress input grows with every session logged, because they send the full history.

## License

MIT
