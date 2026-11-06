# OpenStride, agent-first: a design

*Draft, 2026-11-06. A design only; no code changes come with it.*

## The idea

Today OpenStride is an app with a coach in one tab. The coach has three tools and sees a fixed, pre-assembled context on every message. Everything else, from importing a run to moving a session, is a page, a form or a menu that calls a fixed API route.

The rebuild turns that inside out. **The agent is the application.** The athlete talks to one agent, on the phone or the laptop, and the agent does whatever they ask: sets up the plan, reads the runs, explains a chart, moves a session, logs a sore calf, changes a setting, writes a race report. The app underneath is a workspace the agent works in: the athlete's data as files, a command-line tool for the things code must do, a renderer for the things the athlete needs to see, and one gate. **The only action that waits for the athlete is a change to the training plan.** Everything else the agent does on its own authority.

This is the Omarchy pattern. Omarchy ships an operating system whose every menu action also has a command line, so the agent can drive the system, and it briefs the agent when something happens (a crash, a failed update). The human talks to the agent; the agent uses the system. OpenStride becomes the same thing for one runner's training: an opinionated workspace with a CLI for every action, briefs when something happens, and a conversation on top.

The payoff is the one the README already names: the app gets better when the model does. Today a better model only writes better prose inside the same three tools. In this design a better model reads more of the data, writes better throwaway analysis, follows the skills more carefully and needs fewer guardrails, with no code change.

## What changes

| | Today | Agent-first |
|---|---|---|
| How the athlete uses the app | Pages, forms, menus, plus a Coach tab | One conversation. Pages become cards the agent shows |
| The agent's tools | Three fixed tools (propose, read a run, update notes) | A shell in a workspace, the `openstride` CLI, skills, subagents |
| Context on each message | ~50k tokens of JSON stuffed into every prompt | The agent reads what it needs from files; the workspace guide tells it where things are |
| Who assembles the review, the proposal, the plan | Separate prompts and models per task, wired in code | The same agent, following a skill, calling the CLI for facts and validation |
| Approval | Every proposal; hand edits bypass the coach | Every plan change, hand edits included, through one card. Nothing else |
| Activity data | OpenStride parses .fit and .gpx, syncs Garmin, computes CTL/ATL | intervals.icu holds and analyses activities, wellness and fitness; OpenStride mirrors them |
| Watch delivery | OpenStride builds .fit workouts and pushes to Garmin, unofficially | The plan is pushed to the intervals.icu calendar; intervals.icu sends it to Garmin, Coros, Wahoo and the rest |
| Memory | memory.md, windowed chat | memory.md, a journal, the git history of the workspace, and the harness's own session |
| When the agent acts | When the athlete sends a message, plus one review per import | Also on events: a new activity, the morning, a wellness anomaly, the end of a week, a race |
| Versioning and undo | Hand-rolled versions/ folders and undo keys | The workspace is a git repository. Every change is a commit; undo is a revert |

## Principles, revised

The current split, "the LLM coaches, code handles facts", survives, with two amendments.

1. **The agent operates, code holds the invariants.** Code no longer decides *when* the model is asked or *what* it sees. It owns four things: facts that must be exact (dates, pace arithmetic, VDOT, the plan schema and its validator), the one approval gate, the rendering of what the athlete sees, and the connection to intervals.icu. Everything between those is the agent's.
2. **Data is files, and the files are the API.** The athlete's whole situation is a directory the agent can read with `cat`, `grep`, `jq` and Python. No endpoint stands between the agent and the data. Writes go through the CLI where an invariant exists, and straight to the file where none does.
3. **Prefer the general tool, promote to a dedicated one only to gate, render or audit.** The agent gets a shell first. A dedicated command exists for an action only when the harness needs to intercept it (apply a plan), draw it (show a week) or keep it consistent (compute paces).
4. **Every change is explainable and reversible.** The workspace is a git repository. The agent commits what it changes with a reason; the athlete, or the agent on the athlete's word, can revert any of it.
5. **The plan is sacred.** Nothing writes the plan except `openstride plan apply`, and that needs an approval the athlete gave through the card they were shown. This is enforced in code, not in the prompt.

## Architecture

```
  phone / laptop (PWA)
  ┌──────────────────────────────────────────────┐
  │  glance header: today, form, next session    │
  │  the stream: messages ⇄ cards ⇄ approvals    │
  │  one input box                               │
  └──────────────┬───────────────────────────────┘
                 │ SSE: deltas, status, cards, approval requests
  ┌──────────────┴───────────────────────────────┐
  │  OpenStride server (FastAPI)                 │
  │  · turn queue (one agent turn at a time)     │
  │  · approval gate (mints and checks tokens)   │
  │  · card renderer (fixed card types)          │
  │  · triggers (clock, sync events)             │
  │  · intervals.icu connector (poll in, push out)│
  └──────────────┬───────────────────────────────┘
                 │ one persistent agent session per athlete
  ┌──────────────┴───────────────────────────────┐
  │  Agent runtime (Claude Agent SDK harness)    │
  │  tools: Bash, Read, Write, Edit, Glob, Grep  │
  │  + the `openstride` CLI inside the workspace │
  │  skills: review-run, weekly-review, new-plan,│
  │          daily-brief, race-report, …         │
  │  hooks: deny writes to plan/, log every call │
  └──────────────┬───────────────────────────────┘
                 │ cwd
  ┌──────────────┴───────────────────────────────┐
  │  Workspace = data/ as a git repository       │
  │  plan/  athlete/  activities/  wellness/     │
  │  journal/  outbox/  settings.yaml  GUIDE.md  │
  └──────────────────────────────────────────────┘
                 ▲
                 │ sync (activities, streams, intervals, wellness, zones)
                 ▼ push (planned sessions as calendar events → watch)
          intervals.icu  ←→  Garmin / Coros / Polar / Suunto / Strava
```

Three layers, each with one job:

- **The surface** shows one stream and one input. It renders cards it is given; it never computes anything.
- **The server** keeps the invariants: the gate, the queue, the renderer, the connector, the triggers. It is the part that must be right regardless of the model.
- **The agent** does the coaching and the operating, in a workspace that is just files and a CLI.

## The workspace

The workspace is what the agent sees. It is the current `data/` directory, reorganised so that reading it is enough to understand the athlete, and turned into a git repository.

```
data/
  GUIDE.md                   the workspace guide: what every file is, the CLI, the rules (the agent's CLAUDE.md)
  settings.yaml              units, quiet hours, which triggers are on, model and budget, intervals.icu athlete id
  athlete/
    memory.md                the coach's notes, as today: the one file that outlives everything
    profile.yaml             facts that don't change often: birth year, HR max/rest, zones, thresholds (mirrored from intervals.icu)
  plan/                      READ-ONLY to the agent; written only by `openstride plan apply`
    plan.json                the active plan (the current schema: weeks → sessions → steps, dated)
    request.json             what the plan was built from
    paces.json               reference paces, computed in code
    calendar.json            plan session key → intervals.icu event id, for push and drift checks
  proposals/
    2026-11-06T09-12_next-2-weeks.json   a proposed plan file plus rationale and changes; pending, approved or rejected
  activities/
    2026-11-05T18-02_run.json            the mirror of one intervals.icu activity: summary, intervals.icu's own analysis fields, the agent's review
    2026-11-05T18-02_run.streams.json    the recording (time, distance, HR, pace, cadence, altitude), fetched on first need
    index.jsonl                          one line per activity: id, date, type, distance, time, load, match; the fast way to scan a season
  wellness/
    2026-11.jsonl                        one line per day from intervals.icu: ctl, atl, tsb, ramp, resting HR, HRV, sleep, weight, notes
  journal/
    2026-11-05.md                        what the agent did and concluded that day: the review it wrote, what it asked, what it noticed
    weeks/2026-W45.md                    the weekly review
  outbox/                                cards the agent asked to show, consumed by the server (see Cards)
  logs/
    usage.jsonl                          tokens and cost per turn
```

What this removes: `plans/<id>/versions/`, `undo_keys`, `chat.json` per plan, `metrics_cache.json`, `reviews/`, `raw/`, `series/`. Versions are git history. The chat is the harness session plus the journal. Metrics come from intervals.icu. Reviews live on the activity. Raw files live on intervals.icu.

**Git as the ledger.** The server initialises the repository. Every agent turn ends with a commit of whatever changed, with the agent's one-line reason as the message; every `plan apply` is its own commit tagged with the approval. `git log -- plan/plan.json` is the plan history the Plans page shows today. "Undo that" is a revert the agent runs itself. A corrupted memory file is `git checkout` away. Backups are `git bundle` or a zip of the directory, as today.

**The guide.** `GUIDE.md` replaces the current `APP_GUIDE` prompt, the per-task system prompts and most of `build_user_prompt`. It describes the files, the CLI, the schema of a plan, the units intervals.icu uses, the few hard rules (never write `plan/` directly; sessions after today are not missed; a skipped session was the athlete's choice) and the athlete-facing style. It is the single document to edit when the app changes, and the athlete may read and edit it too.

## The CLI

`openstride` is the one way code gets involved mid-turn. It runs inside the workspace, prints JSON or plain text, and has a `--help` the agent can read. Most reads need no command at all, because the files are there. The commands exist where there is an invariant to keep, a computation that must be exact, or a side effect the harness must own.

**Facts and arithmetic** (pure, no side effects):

- `openstride paces --race 10k --time 44:00` : VDOT and the five reference paces, as `vdot.py` does today
- `openstride dates --race 2027-01-10 --weeks 10` : week start dates, race weekday, days to go
- `openstride plan validate <file>` : the current validator, errors and warnings, on any plan file
- `openstride plan diff <a> <b>` : a session-level diff in the form the proposal card shows

**The plan** (the gated path):

- `openstride plan draft --from <proposed-plan.json> --rationale "…" --scope …` : validates the file, stores it under `proposals/` as pending, prints the proposal id. This is the only way a plan change reaches the athlete. It accepts a whole plan or only the weeks in scope, exactly as `adapt.merge` does today
- `openstride plan apply <proposal-id> --approval <token>` : the gate. Refuses without a valid token, refuses if the plan moved since the proposal was based, re-validates against today, writes `plan/`, commits, pushes the changed sessions to the intervals.icu calendar
- `openstride plan history`, `openstride plan show --version N` : thin wrappers over git

**intervals.icu** (the only network the agent has):

- `openstride icu sync [--since …]` : pull activities, wellness and athlete settings into the mirror; prints what is new
- `openstride icu streams <activity-id>` : fetch and cache one activity's recording
- `openstride icu push [--week N]` : re-send planned sessions as calendar events (also run by `plan apply`)
- `openstride icu upload <file.fit>` : forward a file the athlete dropped in the chat to intervals.icu, then sync
- `openstride icu wellness --date today --note "…" --soreness 3` : write wellness fields the athlete reports in conversation back to intervals.icu

**Showing things** (the only way to put a card in the stream):

- `openstride show week [N|this|next]`, `show session <key>`, `show activity <id>`, `show fitness [--from --to]`, `show volume`, `show plan`
- `openstride show table --from <file.json>` and `openstride show chart --spec <file.json>` for ad-hoc analysis: a Markdown table, or a small line/bar chart over dates, from data the agent computed
- `openstride ask "Which day suits you?" --options Tue Wed Thu` : a choice card, when a question is better tapped than typed

**Housekeeping**:

- `openstride commit -m "…"` : commit the workspace (the harness also does this at the end of every turn)
- `openstride settings set triggers.morning_brief false` : settings the athlete changes by asking

Each command is small and tested on its own, as the modules it wraps are today. The validator, the pace model, the dating of weeks, the proposal merge and the diff all survive as they are.

## The approval gate

**What counts as a plan change:** any write to `plan/`. The first plan, a regenerated draft, a coach proposal, a hand edit ("move Thursday to Saturday"), a skip, a restore of an old version. They all go the same way: the agent builds the new plan file, calls `plan draft`, and the athlete sees one card.

**What does not:** the coach's notes, the journal, a review, logging a session the athlete describes, a wellness note, a setting, deleting a mirrored activity, syncing, re-pushing the calendar, reverting something the agent wrote outside `plan/`. The agent does these and says so in a line.

**The flow:**

1. The agent decides a change is warranted (the athlete asked, or a skill concluded it). It reads the plan, writes the new version to a scratch file, runs `plan validate`, fixes errors itself, and calls `plan draft` with a rationale and a per-session summary of changes.
2. The server stores the proposal, mints an approval token bound to it and to the plan's current git commit, and pushes a **proposal card** into the stream: the rationale, the trade-offs the validator warned about, a week-by-week diff, Approve and Reject.
3. The agent's turn ends with its explanation above the card. Nothing has changed yet.
4. The athlete taps Approve. The server calls `plan apply` with the token. The plan is written, committed, pushed to the calendar. The agent gets a short brief ("Proposal X approved and applied; calendar updated") on its next turn. Reject is recorded with an optional reason, which the agent reads next time.

A trivial edit costs the athlete one tap, which is the price of the rule being uniform. An "auto-approve my own explicit edits" setting is possible later, but it would be the first exception to the one rule, so this design leaves it out. See the open questions.

**Enforcement, in layers:**

- The harness's `PreToolUse` hook denies any Bash, Write or Edit call whose arguments touch `plan/` unless the command is `openstride plan apply`. Hooks run in code, before the tool runs.
- `plan apply` checks the token's signature, the proposal id, and that the plan file's commit is the one the proposal was based on.
- After every turn the server checks `git status` and the hash of `plan/`: a change that did not come through `apply` is reverted and the agent is told, in the next turn, what it tried and why it was undone.
- In the containerised setup (`compose.yaml`), the agent runs in its own container with `plan/` mounted read-only, and the server applies the plan in its own. That makes the hook a convenience and the mount the boundary.

Nothing in the prompt is load-bearing for this.

## The agent runtime

**Harness: the Claude Agent SDK.** It is the Claude Code harness as a library: the agent loop, Bash, file tools, Glob and Grep, subagents, hooks, skills, session persistence with resume, and context compaction, all on the athlete's own machine with their own API key. That is exactly the Omarchy shape, and it means OpenStride writes no agent loop, no tool plumbing and no context management of its own. The server's job shrinks to starting the session with the workspace as its working directory, the guide as its project instructions, the CLI on its path, the hooks registered, and the tool list restricted to Bash, Read, Write, Edit, Glob and Grep.

The cost of this choice is a dependency on the Claude Code runtime (Node) next to the Python server. The `compose.yaml` image carries it; the bare install adds it to the setup. If that dependency proves unwelcome, the fallback is the Anthropic SDK's tool runner with the Anthropic-defined bash and text-editor tools against the same workspace, the same CLI and the same hooks written as runner callbacks. The workspace contract is identical either way, so the choice is reversible.

**One session, serialised.** Each athlete has one persistent agent session, resumed across restarts. Every input, whether a message from the athlete or a brief from a trigger, is a turn on that session, and a queue in the server runs them one at a time. That is what lets the agent say "you asked about this yesterday" and lets a trigger's conclusions land in the same thread the athlete reads. When the harness compacts the session, the journal and `memory.md` are what survive, so the skills tell the agent to write there before a turn ends.

**Skills, not prompts.** The current per-task prompts (plan, regenerate, adapt, review, chat) become Markdown skills in the repository, loaded by the harness when relevant:

- `new-plan`: the conversational intake that replaces the form (goal, race, recent race, days, volume, strength and cross-training), then `paces`, `dates`, a plan written to the schema, `validate`, `plan draft`
- `review-run`: what to look at in a new activity, how to use intervals.icu's interval detection and its load, what to write on the activity file and in the journal, when a trend justifies a proposal and when it does not
- `weekly-review`: the Sunday pass over the week against the plan and the wellness line
- `daily-brief`: the morning card, two lines, and the one question worth asking
- `race-report`: after the race session's date
- `data-analysis`: how to do a quick computation properly (write a short Python script against the index and the streams, show a chart) rather than reasoning over numbers in prose

Skills are text. They get better when the athlete or a contributor edits them, and they hand more judgment to the model as the model earns it. The conservative coaching stance in today's prompts ("one bad run is not a reason to change anything; no change is a good answer") moves into them unchanged.

**Subagents for bulk reading.** Reading twenty activity streams to answer "is my easy pace drifting faster this block" should not sit in the main session's context. The harness's subagents do that: the main agent delegates the scan to a subagent on a cheaper model, with the data-analysis skill, and gets a short result back. The main session stays on the capable model with its context intact.

**Models and effort.** The conversation runs on Claude Opus 5.5 at high effort, as today's default. Trigger-initiated turns run at medium effort. Subagents run on Claude Sonnet 5.5. All three are settings the athlete can change by asking, and a stronger model is a one-line change, which is the point.

## The surface

One screen. The current five tabs, the forms and the menus go.

**The glance header** stays visible at the top: today's session (or "rest"), the form number from intervals.icu, the next hard session and the days to the race. It is the one thing the athlete reads without asking, and it works offline from the last cached state. Tapping anything in it is the same as asking about it.

**The stream** is the conversation, newest at the bottom, with three kinds of entries:

- **Messages**, the agent's and the athlete's. The agent's stream in with a status line ("Reading last week's runs…", "Validating the plan…") from the first second, as today's chat does, because an agentic turn with several tool calls takes longer than a chat reply.
- **Cards**, rendered by the server from fixed types: a week, a session, an activity with its route and pace/HR charts against the planned range, the fitness line, weekly volume, a table, an ad-hoc chart, a choice question. The agent asks for them by `openstride show`; code draws them, the same way every time, which keeps the current principle that the plan is always drawn by one renderer. Cards are live: a week card re-renders when the plan changes.
- **Approval cards**: the proposal with its diff and two buttons. They are the only interactive element besides the input box and the choice card.

**The input** is one text box, with the phone keyboard's dictation for voice, a file drop for a .fit the watch did not upload, and a photo drop for a race bib or a screenshot of a workout the athlete wants copied.

**What the athlete can still do without the agent:** approve or reject a proposal, read cards, scroll the stream, and open a *Data* drawer that lists the workspace files read-only, with the git log. The drawer is for trust and debugging. It is not navigation.

**Offline:** the glance header, this week's card and the last few cards are cached by the service worker, as this week's plan is today. Sending a message queues it until the server is reachable.

**Phone first.** A conversation with cards is a better phone interface than today's tables. The laptop gets the same screen, wider, with the Data drawer open beside the stream.

## intervals.icu as the data hub

intervals.icu becomes the system of record for everything the athlete *did* and *measured*. OpenStride remains the system of record for the *plan* and the *coaching*.

**In:** the connector polls on a timer (every 15 minutes while the server runs, and on demand) and mirrors into the workspace:

- activities, with intervals.icu's own analysis: training load, intensity, pace and HR zone times, detected intervals, efficiency, and the device's extras
- streams, on first need, cached
- wellness per day: fitness, fatigue, form and ramp rate as intervals.icu computes them, resting HR, HRV, sleep, weight, and whatever the athlete logs there
- athlete settings: zones, threshold pace, HR max and rest

Every new activity raises a trigger (below). A `.fit` dropped into the chat goes up to intervals.icu and comes back through the same path, so there is one path.

**Out:** on every `plan apply`, the changed sessions are written to the intervals.icu calendar as structured workout events, with the event id kept in `plan/calendar.json`. intervals.icu pushes planned workouts to the athlete's watch platform. This replaces the Garmin modules, the unofficial login, the `.fit` encoder and the "experimental" push in one move, and covers more watches than Garmin.

**Drift:** if the athlete edits or deletes a planned event in intervals.icu, the next sync notices the difference and briefs the agent, which asks whether to make the plan match (a proposal) or re-push. OpenStride never silently adopts a change to the plan from outside.

**What OpenStride stops doing:** parsing `.fit` and `.gpx`, computing TRIMP, CTL, ATL and TSB, matching by its own rules alone (intervals.icu pairs an activity with the planned event on the same day; the agent handles the exceptions and sets the match on the activity file), talking to Garmin.

**Setup** is two values: the intervals.icu athlete id and API key. The agent asks for them in the first conversation and the server stores them in `data/secrets/`, where the Garmin token lives today. The key never enters the workspace the agent reads; the CLI reads it from the server's environment.

## When the agent acts on its own

Omarchy briefs the agent when the system does something. OpenStride briefs it when the athlete does. Each trigger is a short message queued on the same session, so the agent handles it with the full thread in mind, and the result appears in the stream like any reply.

| Trigger | When | What the agent does, by its skill |
|---|---|---|
| New activity | A sync finds one | Reviews it against the planned session, writes the review on the activity and in the journal, matches it if the automatic match looks wrong, asks one question if something is unclear, drafts a proposal only if a trend warrants one |
| Morning brief | A time the athlete sets; off by default | Two lines: today's session in context, one thing worth knowing from wellness |
| Wellness signal | A sync shows an anomaly intervals.icu flags, or a sharp drop in HRV, sleep or form against the athlete's own baseline | Says so, asks how they feel, may propose an easier day |
| Week end | The last planned day of the week has passed | The weekly review: planned against done, load against form, what the next week needs; proposals if so |
| Plan ends | The day after the race | The race report and the plan-history entry in memory; asks what is next |
| Calendar drift | A sync finds a planned event changed in intervals.icu | Asks what the athlete wants |
| Sync or budget problem | The connector fails, or spend passes the monthly cap in settings | Tells the athlete once, with what to do |

Rules for triggers: quiet hours from settings; never more than one unprompted message in the stream without a reply, except for a new activity; medium effort; the athlete turns any of them off by saying so, which is a settings write the agent does itself.

## Memory

Four layers, each with a job:

1. **`memory.md`** stays exactly as it is: the coach's notes, the one file that outlives plans and sessions, written only by the agent and the athlete, versioned by git instead of the `versions/` folder.
2. **The journal** is new: the agent's day-by-day record of what it did and concluded. It is what makes compaction safe, and it is the first thing the agent reads when asked "what did you say about my calf last week".
3. **The harness session** carries the conversation itself, resumed across restarts and compacted when long.
4. **Git history** holds every change to every file with the agent's reason. The agent can `git log`, `git show` and `git revert` it.

The current rule that the notes must not duplicate what the data holds still applies, and is easier to keep: the data is one `cat` away.

## Cost

Today every chat message carries the whole plan and history, around 50k input tokens. Here the fixed part of a turn is the guide and the skill descriptions, a few thousand tokens cached, and the agent reads only what the question needs. A turn that uses several tools costs more output and more round trips than a single chat reply. The two effects go opposite ways, and the net depends on the question, so the design does not promise a number; it keeps the current habit of logging usage per turn to `logs/usage.jsonl` and shows the month's total in the glance header on request. A monthly cap in settings turns triggers off when reached and tells the athlete.

Levers, all settings: the model per role (main, triggers, subagents), effort per role, which triggers run, and quiet hours.

## Security

- **The API keys** (Anthropic, intervals.icu) stay in the server's environment and `data/secrets/`, outside the workspace. The agent's shell never sees them; the CLI's `icu` commands run through the server.
- **The shell** is confined to the workspace directory and has no network except through `openstride icu`. In the container setup that is a container boundary; bare, it is the hook denying paths outside the workspace plus a user-level sandbox where the platform offers one. This is the athlete's own data on the athlete's own machine, so the threat is a mistake, not an attacker, but the plan directory and the secrets are protected as if it were.
- **Injection** through data: activity names and descriptions arrive from intervals.icu and can contain anything. The guide says they are data. The only consequential action, a plan change, waits for the athlete anyway, and everything else is in git.
- **Password and network exposure** stay as today: localhost or Tailscale, a password beyond that, refusal to start open without one.

## What stays, what goes

| Module today | Fate |
|---|---|
| `vdot.py`, `validate.py`, `models.py` (plan schema), `clock.py` | Stay, behind `openstride paces`, `plan validate`, `dates` |
| `adapt.merge`, `adapt.check`, the diff rendering | Stay, behind `plan draft` and `plan diff` |
| `lifecycle.py`, `storage.py` versions and undo | Replaced by git and `plan apply` |
| `edits.py` | Its rules (no two runs a day, nothing into the past, race fixed) move into the validator; the operations become the agent editing the plan file |
| `chat.py`, `prompts/*` | Replaced by the harness, `GUIDE.md` and the skills |
| `review.py`, `generator.py`, `adapt.create_proposal` | Become skills; the model writes the plan file directly and the CLI validates it |
| `activities.py`, `series.py`, `workout_fit.py`, `metrics.py`, `garmin/*` | Removed; intervals.icu does this |
| `memory.py` | Stays as a file convention; git replaces its versioning |
| `static/` | Rewritten as the stream and the card renderer; `charts.js` survives inside the activity and fitness cards |
| Tests | The fact modules keep theirs. New tests: the gate (every way around it), the card renderer, the connector against a fake intervals.icu, the triggers, and the skills against the demo workspace with a recorded model |
| The offline demo | Stays, as a demo workspace with a recorded session, and is how the surface is developed without an API key |

## Phases

Each phase ships on its own and leaves the current app working until the last one.

1. **intervals.icu in, Garmin out.** The connector, the mirror layout, the calendar push. The existing coach reads the mirror instead of its own parsers. Deletes the most code for the least risk, and decides the data layout the rest depends on.
2. **The workspace and the CLI.** `data/` becomes a git repository with the layout above; the CLI exposes the fact modules and the gated `plan draft`/`plan apply`. Nothing user-facing changes yet; the current routes call the CLI.
3. **The agent runtime behind the Coach tab.** The harness replaces `chat.py`, with the guide, the hooks and the first skills (`review-run`, `new-plan`). The approval card appears under the coach's replies, as proposals do today. The rest of the app is still there for anything the agent does not yet do well, which is how the skills get tuned.
4. **The stream.** The new surface replaces the tabs: glance header, stream, cards, input, Data drawer. The old pages are removed as each becomes a card.
5. **Triggers.** New activity, week end, plan end first; the morning brief and wellness signals once the first three feel right.
6. **Harden.** The container split with `plan/` read-only, the budget cap, the hosted-version questions.

## Open questions

- **Approving trivial edits.** Uniform rule, one tap each, or an exception for the athlete's own explicit edits? This design keeps the rule.
- **The Node runtime.** Is a dependency on the Claude Code harness acceptable for the bare install, or should the fallback harness be the default from the start?
- **How much to show without asking.** The glance header is the minimum. A tap on "this week" could open a week card without a message. That is still the agent's data and renderer; it is only a question of whether a tap counts as a message.
- **File uploads.** Keep the drop-a-`.fit` path (forwarded to intervals.icu) or require every activity to come through intervals.icu?
- **Hosted later.** The workspace and CLI design ports to a managed sandbox per athlete without change. Whether that is the hosted version, and what it would cost per athlete, is a separate decision.
