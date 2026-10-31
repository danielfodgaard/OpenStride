# OpenStride

**An open-source running coach: a Runna-style training app where the coaching is done by an LLM and the app is a thin layer of JSON on top.**

> Built in one day (31 October 2026) at Autonomous SG 2026 for the NUS Hackers challenge: pick an expensive, opaque app, figure out how it works, and build an open version. MIT licensed.

---

## TL;DR

| | Runna | OpenStride |
|---|---|---|
| Price | $19.99/month or $119.99/year (US, Oct 2026) | $0 software, MIT licence |
| Running cost | included | your own Claude API key: about $0.45–3.50 per month (see [cost](#before--after-cost)) |
| See your whole plan? | only on the annual plan | always: it's a JSON file on your disk |
| Why did my plan change? | not shown | every change comes with a written reason and a diff you accept or reject |
| Your data | their servers | a folder on your computer |

---

## Why Runna

I'm training for a half marathon next year, and like a lot of runners I looked at Runna. It's a good app. It has also become an expensive one.

- **It costs $19.99 a month or $119.99 a year in the US** (the annual plan works out to $9.99 a month), or $149.99 a year bundled with Strava. ([SensAI, checked 21 Oct 2026](https://www.sensai.fit/blog/runna-vs-trainingpeaks-2026); [the5krunner, Oct 2026](https://the5krunner.com/2026/10/14/is-runna-free/))
- **The free plans are gone.** The beginner plans (New to Running, Return to Running, Path to parkrun) moved behind the paywall on 5 June 2026, so someone doing couch-to-5K now pays the same as a marathoner. ([SensAI](https://www.sensai.fit/blog/runna-vs-trainingpeaks-2026); [stack-archive](https://stack-archive.com/blog/is-runna-free-price-trial-strava-bundle-2026/))
- **The monthly plan only shows you the month you've paid for.** To see your whole build before committing, you need the annual subscription. ([SensAI](https://www.sensai.fit/blog/runna-vs-trainingpeaks-2026))
- **Strava no longer lets you create new running plans on its own site.** New plans come from Runna, which a standard Strava subscription doesn't include. ([stack-archive](https://stack-archive.com/blog/is-runna-free-price-trial-strava-bundle-2026/))

And a lot of people pay it:

- **1,000,000+ downloads on Google Play** ([store figures via clickyourapp](https://clickyourapp.com/au/charts/app-store/com.runbuddy.prod/)). AppGoblin estimates about 3.9M Android installs, growing by roughly 230,000 a month ([AppGoblin, crawled 29 Oct 2026](https://www.appgoblin.info/apps/com.runbuddy.prod)).
- **4.9★ from about 35,100 US App Store ratings** ([SensAI, citing the App Store, 21 Oct 2026](https://www.sensai.fit/blog/runna-vs-trainingpeaks-2026)).
- **Owned by Strava**, which had 150M+ registered users when it bought Runna in April 2025 ([Advnture](https://www.advnture.com/tech/apps-software/a-perfect-fit-stravas-150-million-users-get-a-new-running-coach-as-brand-acquires-coaching-app-runna)).

---

## What I uncovered: how Runna actually works

I didn't decompile anything or touch Runna's servers. Everything below comes from Runna's own public statements and support pages, as compiled by [SensAI's Oct 2026 comparison](https://www.sensai.fit/blog/runna-vs-trainingpeaks-2026), which links each claim to its source.

1. **It's not an LLM.** Runna told TechRadar in February 2026 that its plans are "not created by AI": coaches design the plans and "an algorithm then tailors and adapts those coach-designed plans to each runner."
2. **Your inputs set a starting point.** Your ability level sets your starting weekly mileage and longest run; recent race times set your initial pace targets.
3. **Progression is rule-based.** Weekly mileage rises under a fixed ceiling per week. Experienced runners get higher targets, but not faster build rates.
4. **Adaptation is a suggestion.** As you train, Runna may suggest raising or lowering your pace targets, and you choose whether to accept.

So underneath the subscription, Runna is **a library of coach-written templates, a pace model from race times, a few progression rules, and an accept/decline loop for pace changes.** None of that is hard to compute. Pace models from race times have been public for decades (Jack Daniels' VDOT tables), and mileage ceilings and taper rules are in every coaching book.

What *is* opaque is the reasoning. Runna will nudge your paces, but it won't tell you why in any detail, and you can't ask it a follow-up question.

That gave me the idea for the rebuild: **the coaching knowledge Runna encodes as hand-written rules is exactly what a modern LLM already knows.** So instead of re-implementing the rules, OpenStride hands the coaching to the model and keeps the app as thin as possible.

---

## The design principle: the LLM coaches, code handles facts

Hardcoding coaching logic isn't worth it. Models keep getting better; a rulebook I write today doesn't. So OpenStride sends the model as much context as possible and keeps the app simple.

But "just ask the LLM" breaks in predictable ways: a wrong date, a pace that's 4:14 when it should be 4:41, a plan that schedules a run on a day you said you can't train. On a live demo, one of those costs more than the 30 lines of code that would have prevented it.

So the split is:

| The LLM decides | Code does |
|---|---|
| Periodisation, weekly volume, session choice, taper | **Facts:** dates, pace arithmetic, parsing .fit/.gpx files into summaries |
| Whether to adjust the reference paces (and says why) | **Structure:** JSON schemas, and one renderer that always draws a plan the same way |
| How to read a vague goal ("sub-1:30, stay injury-free") | **Verification:** a thin validator that *reports* problems back to the LLM and never fixes them itself |
| Run reviews, plan adaptations, coach chat | **Control:** *when* the LLM is asked; you approve every change |

Two consequences:

- **The app gets better when the model does.** Change the model name in `.env` and the coaching improves, with no code changes.
- **Every decision is explainable.** The model has to return structured JSON with a `rationale` field, so you always see the reason behind a change. That's the thing Runna doesn't give you.

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

### Tracking

- **Import .fit and .gpx files** from any watch. Each file is parsed into a compact summary (distance, time, laps, km splits, heart rate, cadence) and matched to the planned session on that date. The raw file never goes to the LLM; the model gets the summary, which keeps each call small and cheap.
- **Every imported run gets an automatic review** from a small, cheap model: did you hit the purpose of the session, and is anything worth flagging ("HR high for an easy run")? Reviews never change the plan.
- **Strength and cross-training** sessions can be logged by hand or imported (rides and swims match cross-training sessions).

### Adaptation: one bad day shouldn't rewire your plan

**Nothing changes your plan unless you ask, and then accept.**

1. You ask for plan changes, choose a scope (this week / next 2 weeks / rest of plan) and can add context ("calf niggle since Tuesday").
2. The model gets everything: the plan, every run summary and review, your notes, the scope.
3. It's told to be conservative. A single bad session is not a reason to change anything, and **"no change recommended" is a valid, good answer.**
4. If it proposes changes, you get a rationale, a one-line reason per change, and a diff of the affected weeks. Accepting creates a new plan version; you can always restore an older one.

### A coach you can talk to

The coach chat sees your plan, every run, every review and your training-load numbers. Ask "is my easy pace too fast?" and you get an answer that cites your actual runs. Ask "can I swap Thursday and Saturday next week?" and the request goes through the same proposal → diff → accept flow. The chat can never edit your plan directly.

### Training load, in code

Fitness, fatigue and form (CTL / ATL / TSB) are computed in code from a Banister-style TRIMP load per session and drawn next to weekly planned-vs-actual volume. The same numbers go into every prompt, so the chat, reviews and adaptations all reason from one set of facts.

---

## How I know it works

- **216 automated tests** (`pytest`) cover the pace model, the validator (each rule triggered by a mutated sample plan), .fit/.gpx parsing and matching, plan lifecycle and versioning, proposal merging, chat actions, metrics and the API.
- **Test activities with known outcomes** in `examples/activities`: synthetic runs that match the sample plan (interval laps, heart-rate drift, a deliberately bad run, unplanned runs) plus real edge cases (a cycling file, GPS tracks without heart rate). Each has an expected match and review.
- **An offline demo** with a full plan, three weeks of reviewed runs, a chat thread and a pending proposal, which runs without an API key (see below).
- **Measured costs.** Every LLM call logs its token usage, so the costs below come from the app, not from guesses.

---

## Before / after cost

OpenStride's software is free. The only running cost is your own Claude API usage, at [Anthropic's list prices](https://platform.claude.com/docs/en/about-claude/pricing) (Sonnet 5.5: $2 / $10 per million input / output tokens; Haiku 5.5: $0.10 / $0.50). Hosting is $0: it runs on your own computer.

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

**Per user per month**, built from the measured numbers (5 runs a week, one 10-week plan plus one regeneration on Sonnet, spread over its 2.3 months):

| Usage | OpenStride | Runna monthly | Runna annual (per month) |
|---|---|---|---|
| Light: plan + every run reviewed + a weekly adaptation check | **~$0.44** | $19.99 | $9.99 |
| Heavy: the above + 4 progress summaries + 20 chat messages + 2 proposals | **~$3.50** | $19.99 | $9.99 |
| **Saving** | | **82–98%** | **65–96%** |

Chat is most of the heavy-use cost, because every message sends the full plan and history, and that input grows as the plan goes on. Prompt caching (cache reads on Sonnet 5.5 cost 5% of the input price) would cut most of it.

---

## Run it

You need Python 3.11+ and a [Claude API key](https://platform.claude.com/).

```bash
git clone https://github.com/danielfodgaard/OpenStride.git
cd OpenStride
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then set ANTHROPIC_API_KEY
.venv/bin/uvicorn app.main:app
```

Open http://127.0.0.1:8000. Your plans, runs, reviews and chat are stored as JSON files in `data/`.

**Offline demo, no API key needed:**

```bash
OPENSTRIDE_DEMO=1 .venv/bin/uvicorn app.main:app
```

This serves a throwaway copy of the demo data with the date pinned to 25 November 2026: an active half-marathon plan, three weeks of reviewed runs, logged strength sessions, a cycling file, a chat thread with a pending proposal, and the charts. Your own `data/` is not touched, and every change disappears when the server stops. Actions that need Claude show an error unless a key is set in `.env`.

**Tests:**

```bash
.venv/bin/pytest
```

**Models:** set per task in `.env` (`PLAN_MODEL`, `ADAPT_MODEL`, `REVIEW_MODEL`, `PROGRESS_MODEL`, `CHAT_MODEL`) to trade quality for cost.

---

## What's next

- **Daily use:** a mobile-first design and installable app, one server for phone and laptop over Tailscale, automatic run sync from Garmin Connect, and planned sessions sent to the watch as structured workouts.
- **Nutrition:** race-day fuelling and day-to-day guidance by session type, focused on eating enough for the training load.
- **Then:** one-command install for technical testers, and eventually a hosted version for everyone else.

---

## Licence

MIT, see [LICENSE](LICENSE). The dependencies are under permissive licences compatible with MIT: FastAPI, Pydantic, the Anthropic SDK, fitdecode and pytest (MIT), uvicorn and python-dotenv (BSD-3-Clause), gpxpy and python-multipart (Apache-2.0). OpenStride isn't affiliated with Runna or Strava; no Runna code, assets or trademarks are used.

---

## Sources

- [SensAI – Runna vs TrainingPeaks 2026](https://www.sensai.fit/blog/runna-vs-trainingpeaks-2026) (prices, how Runna builds and adapts plans, App Store rating; checked 21 Oct 2026)
- [the5krunner – Is Runna free?](https://the5krunner.com/2026/10/14/is-runna-free/) (prices by region, Oct 2026)
- [stack-archive – Is Runna free? Price, trial, Strava bundle 2026](https://stack-archive.com/blog/is-runna-free-price-trial-strava-bundle-2026/)
- [Runna support – How Runna builds your plan around your current fitness](https://support.runna.com/en/articles/15231838-how-does-runna-build-your-training-plan-around-your-current-fitness) (as cited by SensAI)
- [clickyourapp – Runna store figures (Google Play 1,000,000+ downloads)](https://clickyourapp.com/au/charts/app-store/com.runbuddy.prod/)
- [AppGoblin – Runna Android install estimate](https://www.appgoblin.info/apps/com.runbuddy.prod) (crawled 29 Oct 2026)
- [Advnture – Strava's 150 million users get a new running coach](https://www.advnture.com/tech/apps-software/a-perfect-fit-stravas-150-million-users-get-a-new-running-coach-as-brand-acquires-coaching-app-runna)
- [Anthropic – Claude API pricing](https://platform.claude.com/docs/en/about-claude/pricing)
