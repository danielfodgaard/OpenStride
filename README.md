# OpenStride

Open-source running coach. Generate a training plan, import the runs you actually do, get each one reviewed, and adapt the plan when you choose to.

## Design principle

OpenStride is a thin app on top of the LLM. The LLM makes every coaching judgment. Code only:

- parses files and does arithmetic and dates (facts),
- defines JSON schemas and renders them (structure),
- runs thin checks whose errors go back to the LLM to fix (verification), and never fixes a plan itself,
- controls when the LLM is asked and lets you approve the result (control).

The LLM gets as much context as is relevant: the full request, the plan, every run summary, review and note. Nothing changes the plan unless you ask for suggestions and then press Accept.

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

This serves a throwaway copy of `examples/demo/data` with the date pinned to Wednesday 11 November 2026: an active half-marathon plan, a week of runs with real reviews, one bad run with a calf-pain note, a progress summary and a pending proposal. Your `data/` is not touched and every change disappears when the server stops.

Offline you can open the plan, expand reviews, accept or reject the proposal, view and restore versions, and upload `examples/activities/2026-11-06_shakeout_unplanned.gpx`, which imports with a "review failed" message. Actions that need Claude show an error; with a key in `.env` they work in the demo too.

## Test

    .venv/bin/pytest

## How paces work

`app/vdot.py` applies the Daniels–Gilbert formulas to your recent race. Those paces are a reference: Claude may adjust them if your context justifies it, and says why in the summary.

## Models and cost

Set per task in `.env`: `PLAN_MODEL`, `ADAPT_MODEL`, `PROGRESS_MODEL` (default Claude Sonnet 5.5) and `REVIEW_MODEL` (default Claude Haiku 5.5). The v1 name `CLAUDE_MODEL` still works as a fallback for `PLAN_MODEL`.

Every saved LLM output records its token usage, and the plan page shows the running total. Measured from real calls on the sample half-marathon plan, at list prices of $2/$10 per million tokens for Sonnet 5.5 and $0.10/$0.50 for Haiku 5.5:

| Action | Model | Tokens in | Tokens out | Cost |
|---|---|---|---|---|
| Generate a 10-week plan | Haiku 5.5 | 2,282 | 11,593 | $0.006 |
| Regenerate with feedback | Haiku 5.5 | 8,634 | 14,051 | $0.008 |
| Review a run (average of 5) | Haiku 5.5 | 2,737 | 488 | $0.0005 |
| Adaptation, no change | Sonnet 5.5 | 20,113 | 338 | $0.044 |
| Adaptation, with changes | Sonnet 5.5 | 20,164 | 1,691 | $0.057 |
| Progress summary | Sonnet 5.5 | 19,933 | 1,560 | $0.055 |

Plan generation has not been measured on the default Sonnet 5.5. The same token counts would cost about $0.12, but tokens differ between models. Adaptation and progress input grows with each run imported.

## License

MIT
