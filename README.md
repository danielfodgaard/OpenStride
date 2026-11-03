# OpenStride

**An open-source running coach: the coaching is done by an LLM, and the app is a thin layer of JSON on top.**

Your plan, your runs and your coach's reasoning live as files on your own computer. Every change to the plan comes with a written reason, and nothing changes unless you accept it. MIT licensed.

---

## The design principle: the LLM coaches, code handles facts

Hardcoding coaching logic isn't worth it. Models keep getting better; a rulebook I write today doesn't. So OpenStride sends the model as much context as possible and keeps the app simple.

But "just ask the LLM" breaks in predictable ways: a wrong date, a pace that's 4:14 when it should be 4:41, a plan that schedules a run on a day you said you can't train. Each of those costs more than the 30 lines of code that would have prevented it.

So the split is:

| The LLM decides | Code does |
|---|---|
| Periodisation, weekly volume, session choice, taper | **Facts:** dates, pace arithmetic, parsing .fit/.gpx files into summaries |
| Whether to adjust the reference paces (and says why) | **Structure:** JSON schemas, and one renderer that always draws a plan the same way |
| How to read a vague goal ("sub-1:30, stay injury-free") | **Verification:** a thin validator that *reports* problems back to the LLM and never fixes them itself |
| Run reviews, plan adaptations, coach chat | **Control:** *when* the LLM is asked; you approve every change, and your own edits never go through the LLM |

Two consequences:

- **The app gets better when the model does.** Change the model name in `.env` and the coaching improves, with no code changes.
- **Every decision is explainable.** The model has to return structured JSON with a `rationale` field, so you always see the reason behind a change.

---

## What it does

### Plan generation

You describe your goal, a recent race, your current weekly volume, which days you can train, your long-run day, and optionally strength and cross-training sessions.

- **Reference paces in code.** OpenStride computes your VDOT from the recent race with the Daniels–Gilbert formulas and derives easy / marathon / threshold / interval / repetition paces. These go to the model as *reference data*. It may adjust them if your context justifies it, and it has to say so in the plan summary.
- **Structured outputs.** The model returns a plan that must match a strict JSON schema (weeks → sessions → steps).
- **Errors vs warnings.**
  - **Errors** are constraints *you* set: sessions only on your available days, the right number of weeks, the race on race day, nothing in the past modified. They go back to the model once, with a "fix these problems" message.
  - **Warnings** are coaching judgments: back-to-back hard days, a big week-over-week volume jump. They never block anything; they're shown next to the plan so you can see the trade-off.

  My first version treated "long run on your long-run day" and "no consecutive hard days" as hard rules. Then I asked the coach to swap Thursday and Saturday for one week, and it silently dropped an interval session to satisfy the rules. Those rules were coaching judgments pretending to be facts, so they became warnings.
- **Plans are drafts until you accept them.** You can regenerate with feedback ("long run on Sunday instead", "too much volume early on"); only an accepted plan becomes your active plan. Old plans are archived, not deleted.

### Editing the plan by hand

Some changes are yours to make, not the coach's. The "⋯" menu on every session applies them at once, with no LLM call, no cost and an Undo button:

- **Move** a session to another day, or **swap** two sessions in the same or adjacent weeks. On a laptop you can drag a session onto a day or another session.
- **Change** what a session is: distance, duration, target pace, type, title and description. The week's planned volume follows, and an interval session becomes one steady block when its amount or pace changes.
- **Skip** a session, and unskip it later. A skipped session is your choice and is never counted as missed.
- **Tell the plan what a run really was.** Under a workout: "This was my …" counts it as a planned session on a nearby day, and "I did this instead of …" marks the planned session as replaced.

Each edit is a new plan version with a note the coach can read, so a later review or adaptation knows what moved and why. Workouts already on your Garmin follow the edit: a moved or changed session is re-sent, a skipped one is removed. Edits only warn about coaching judgments ("Wed isn't one of your usual training days", "hard runs on consecutive days"); facts, like two runs on one day or moving a session into the past, are refused.

### Tracking

- **Import .fit and .gpx files** from any watch. Each file is parsed into a compact summary (distance, time, laps, km splits, heart rate, cadence) and matched to the planned session on that date. The raw file never goes to the LLM; the model gets the summary, which keeps each call small and cheap.
- **Every imported run gets an automatic review** from a small, cheap model: did you hit the purpose of the session, and is anything worth flagging ("HR high for an easy run")? Reviews never change the plan.
- **Strength and cross-training** sessions can be logged by hand or imported (rides and swims match cross-training sessions).

### On your phone and your watch

- **A Workouts page and a page per workout**: route, pace against the planned range, heart rate and laps.
- **An installable app.** Install it from the phone's browser; this week's plan and opened workouts stay readable offline.
- **One server for phone and laptop.** Over [Tailscale](https://tailscale.com/), your phone reaches the OpenStride running on your laptop with HTTPS, and nothing is exposed to the internet.
- **Garmin, from your phone.** Sync runs from Garmin Connect through the official Personal API. Download planned sessions as structured workouts and copy them to your watch over USB, which is verified on a real watch. Send them straight to the watch from the app (experimental: not yet tested against a real Garmin account).

### Adaptation: one bad day shouldn't rewire your plan

**Nothing changes your plan unless you ask, and then accept.**

1. You ask for plan changes, choose a scope (this week / next 2 weeks / rest of plan) and can add context ("calf niggle since Tuesday").
2. The model gets everything: the plan, every run summary and review, your notes and hand edits, the scope.
3. It's told to be conservative. A single bad session is not a reason to change anything, and **"no change recommended" is a valid, good answer.**
4. If it proposes changes, you get a rationale, a one-line reason per change, and a diff of the affected weeks. Accepting creates a new plan version; you can always restore an older one.

After a hand edit, the toast offers "Ask the coach to check this week", which starts an adaptation with your edit as context.

### A coach you can talk to

The coach chat sees your plan, every run, every review and your training-load numbers. Ask "is my easy pace too fast?" and you get an answer that cites your actual runs. Ask "can I swap Thursday and Saturday next week?" and the request goes through the same proposal → diff → accept flow. The chat can never edit your plan directly.

### Training load, in code

Fitness, fatigue and form (CTL / ATL / TSB) are computed in code from a Banister-style TRIMP load per session and drawn next to weekly planned-vs-actual volume. The same numbers go into every prompt, so the chat, reviews and adaptations all reason from one set of facts.

---

## How I know it works

- **730 automated tests** (`pytest`) cover the pace model, the validator (each rule triggered by a mutated sample plan), .fit/.gpx parsing and matching, plan lifecycle and versioning, hand edits and undo, proposal merging, chat actions, Garmin sync and delivery against fakes, metrics and the API.
- **Test activities with known outcomes** in `examples/activities`: synthetic runs that match the sample plan (interval laps, heart-rate drift, a deliberately bad run, unplanned runs) plus real edge cases (a cycling file, GPS tracks without heart rate). Each has an expected match and review.
- **An offline demo** with a full plan, three weeks of reviewed runs, a chat thread and a pending proposal, which runs without an API key (see below).
- **Measured costs.** Every LLM call logs its token usage, so the costs below come from the app, not from guesses.

---

## What it costs

OpenStride's software is free. The only running cost is your own Claude API usage, at [Anthropic's list prices](https://platform.claude.com/docs/en/about-claude/pricing) (Sonnet 5.5: $2 / $10 per million input / output tokens; Haiku 5.5: $0.10 / $0.50). Hosting is $0: it runs on your own computer. Hand edits cost nothing.

**Measured per call**, on a sample half-marathon plan with three weeks of training logged:

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

Plan generation was measured on Haiku. On the default Sonnet 5.5, the same token counts cost about $0.24 per plan and $0.16 per regeneration.

**Per month**, built from the measured numbers (5 runs a week, one 10-week plan plus one regeneration on Sonnet, spread over its 2.3 months):

| Usage | Cost per month |
|---|---|
| Light: plan + every run reviewed + a weekly adaptation check | **~$0.44**, plus ~$0.10 once for the plan |
| Heavy: the above + 4 progress summaries + 20 chat messages + 2 proposals | **~$3.50** |

Chat is most of the heavy-use cost, because every message sends the full plan and history, and that input grows as the plan goes on. Prompt caching (cache reads on Sonnet 5.5 cost 5% of the input price) would cut most of it.

---

## Run it

You need [uv](https://docs.astral.sh/uv/) and a [Claude API key](https://platform.claude.com/).

```bash
git clone https://github.com/danielfodgaard/OpenStride.git
cd OpenStride
uv sync
cp .env.example .env        # then set ANTHROPIC_API_KEY
uv run openstride
```

Open http://127.0.0.1:8000. Your plans, runs, reviews and chat are stored as JSON files in `data/`.

**Offline demo, no API key needed:** `OPENSTRIDE_DEMO=1 uv run openstride`

**Tests:** `uv run pytest`

**Phone access, Garmin, password and backups:** see [docs/SETUP.md](docs/SETUP.md).

**Models:** set per task in `.env` (`PLAN_MODEL`, `ADAPT_MODEL`, `REVIEW_MODEL`, `PROGRESS_MODEL`, `CHAT_MODEL`) to trade quality for cost.

---

## What's next

- **Send to Garmin, verified:** testing workout sending against a real Garmin account.
- **Nutrition:** race-day fuelling and day-to-day guidance by session type, focused on eating enough for the training load.
- **Then:** one-command install for technical testers, and eventually a hosted version for everyone else.

---

## Licence

MIT, see [LICENSE](LICENSE). The dependencies are under permissive licences compatible with MIT: FastAPI, Pydantic, the Anthropic SDK, fitdecode, filelock, garminconnect and pytest (MIT), uvicorn and python-dotenv (BSD-3-Clause), gpxpy, python-multipart and garmin-connect-personal-api-python (Apache-2.0).
