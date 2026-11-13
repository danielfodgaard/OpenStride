# OpenStride, agent-first: a design

*Draft, 2026-11-06, revised with the write boundary and the analyst, and on 2026-11-07 with providers. A design only; no code changes come with it.*

## Status

Built on the `agent-first` branch as version 0.2.0. The design below is what was built, with these differences:

- **The CLI reaches the server through files, not a socket.** A request is a file in the turn's spool folder inside the workspace (`app/spool.py`). The OS sandbox closes sockets along with the network; files are what every sandbox and a shared volume have in common.
- **A tap on the glance header opens the card directly**, without a message to the agent. It is the same card from the same builder, instantly and for free.
- **Usage is kept in the server's state folder**, not under `logs/` in the workspace, and read with `openstride usage`.
- **`GUIDE.md`, `proposals/` and `reports/` are written only by the server.** The guide tracks the app's version; what the athlete wants the coach to hold to goes in `athlete/memory.md`.
- **The boundary on a bare install is the harness's OS sandbox** (Seatbelt, or bubblewrap), probed at start. The two-container split is not built.
- **Hand-logged sessions and wellness notes are written to the provider**, at the athlete's word, and come back through the sync. They are the athlete's own input, not a change to what the watch recorded.
- **New activities are briefed as one turn per sync**, and the first sync is history, not news.
- **The local provider from the old parsers is not built.** The second implementation of the contract is a fixture provider that reads neutral-shape files, which is also the offline demo.
- **In Docker the sandbox needs `seccomp=unconfined` on the container**, and then runs in a weaker form unless `systempaths=unconfined` is added too. Both were run with the real agent.
- **Not yet verified:** a full first sync against a long history, and pace targets on a real watch.

## The idea

Today OpenStride is an app with a coach in one tab. The coach has three tools and sees a fixed, pre-assembled context on every message. Everything else, from importing a run to moving a session, is a page, a form or a menu that calls a fixed API route.

The rebuild turns that inside out. **The agent is the interface, in place of the GUI.** The athlete talks to one agent, on the phone or the laptop, and the agent does whatever they ask: sets up the plan, reads the runs, explains a chart, moves a session, logs a sore calf, changes a setting, writes a race report. The app underneath is a workspace the agent works in: the athlete's data as files, a command-line tool for the things code must do, a renderer for the things the athlete needs to see, and one gate. **The only action that waits for the athlete is a change to the training plan.** Everything else the agent does on its own authority, within a write boundary: it may write what it authors, it may never write what the watch recorded.

Behind the interface sits a second agent, **the analyst**: a data-scientist that runs in a sandbox over a read-only copy of the data, computes rather than estimates, and comes back with reports, charts and proposals. New plans, a block review, a drift in easy pace, a shoe that is due for retirement: the analyst's findings reach the athlete through the same stream and the same approval card.

Underneath both, **OpenStride is a framework, not a bundle.** It does not own the analysis of recorded data; it integrates whatever service or tool does. intervals.icu is the first *provider*, because it analyses files well, covers most watches, and pushes planned workouts back to them. TrainingPeaks, Strava, Runalyze or a local tool can be providers too, behind the same contract, without the agents, the workspace or the plan noticing. The same is true of the agent harness and the model. The framework is the workspace, the CLI, the gate, the cards and the provider contract; everything that plugs into it is replaceable.

This is the Omarchy pattern. Omarchy ships an operating system whose every menu action also has a command line, so the agent can drive the system, and it briefs the agent when something happens (a crash, a failed update). The human talks to the agent; the agent uses the system. OpenStride becomes the same thing for one runner's training: an opinionated workspace with a CLI for every action, briefs when something happens, and a conversation on top.

The payoff is the one the README already names: the app gets better when the model does. Today a better model only writes better prose inside the same three tools. In this design a better model reads more of the data, writes better throwaway analysis, follows the skills more carefully and needs fewer guardrails, with no code change.

## What changes

| | Today | Agent-first |
|---|---|---|
| How the athlete uses the app | Pages, forms, menus, plus a Coach tab | One conversation. Pages become cards the agent shows |
| The agent's tools | Three fixed tools (propose, read a run, update notes) | A shell in a workspace, the `openstride` CLI, skills, subagents |
| Context on each message | ~50k tokens of JSON stuffed into every prompt | The agent reads what it needs from files; the workspace guide tells it where things are |
| Who assembles the review, the proposal, the plan | Separate prompts and models per task, wired in code | The interface agent for the everyday; the analyst, in a sandbox, for anything that needs computation |
| Approval | Every proposal; hand edits bypass the coach | Every plan change, hand edits included, through one card. Nothing else |
| Activity data | OpenStride parses .fit and .gpx, syncs Garmin, computes CTL/ATL | A provider holds and analyses activities, wellness and fitness; OpenStride mirrors them through one contract. intervals.icu first |
| Watch delivery | OpenStride builds .fit workouts and pushes to Garmin, unofficially | The plan is pushed to the provider's calendar where it has one; intervals.icu sends it to Garmin, Coros, Wahoo and the rest |
| Memory | memory.md, windowed chat | memory.md, a journal, the git history of the workspace, and the harness's own session |
| When the agent acts | When the athlete sends a message, plus one review per import | Also on events: a new activity, the morning, a wellness anomaly, the end of a week, a race |
| Versioning and undo | Hand-rolled versions/ folders and undo keys | The workspace is a git repository. Every change is a commit; undo is a revert |

## Principles, revised

The current split, "the LLM coaches, code handles facts", survives, with two amendments.

1. **The agent operates, code holds the invariants.** Code no longer decides *when* the model is asked or *what* it sees. It owns four things: facts that must be exact (dates, pace arithmetic, VDOT, the plan schema and its validator), the one approval gate, the rendering of what the athlete sees, and the connection to intervals.icu. Everything between those is the agent's.
2. **Data is files, and the files are the API.** The athlete's whole situation is a directory the agent can read with `cat`, `grep`, `jq` and Python. No endpoint stands between the agent and the data. Writes go through the CLI where an invariant exists, and straight to the file where none does.
   **Three tiers of write access**, enforced in code: *recorded data* (activities, streams, wellness, athlete settings mirrored from intervals.icu) is never written by any agent; *the plan* is written only through the approval gate; *authored data* (notes, journal, reviews, reports, matches, settings, proposals) the agent writes freely and git keeps.
3. **Prefer the general tool, promote to a dedicated one only to gate, render or audit.** The agent gets a shell first. A dedicated command exists for an action only when the harness needs to intercept it (apply a plan), draw it (show a week) or keep it consistent (compute paces).
4. **Every change is explainable and reversible.** The workspace is a git repository. The agent commits what it changes with a reason; the athlete, or the agent on the athlete's word, can revert any of it.
5. **The plan is sacred, and the record is untouchable.** Nothing writes the plan except `openstride plan apply`, and that needs an approval the athlete gave through the card they were shown. Nothing writes what the watch recorded except the connector that mirrors it. Both are enforced in code, not in the prompt.
6. **Compute, don't estimate.** When an answer rests on numbers across many runs, the agent hands the question to the analyst, which writes code over the data in a sandbox and returns a result it can show. The model's judgment goes into what to ask and what the result means, not into arithmetic.
7. **The framework owns the contract, not the analysis.** OpenStride defines what a provider must supply (activities with their analysis, streams, wellness, athlete settings) and what it may supply (a calendar to push to, uploads, gear), and how that lands in the workspace. Which service does the analysis is a setting. The same holds one level up: the agent harness and the model are chosen in settings, and the workspace, the CLI and the gate do not change when they do.

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
  │  Interface agent (Claude Agent SDK harness)  │
  │  tools: Bash, Read, Write, Edit, Glob, Grep  │
  │  + the `openstride` CLI inside the workspace │
  │  skills: review-run, weekly-review, new-plan,│
  │          daily-brief, race-report, …         │
  │  hooks: deny writes to plan/ and recorded    │
  │         data, log every call                 │
  └──────┬───────────────────────┬───────────────┘
         │ cwd                   │ `openstride analyse "…"`
  ┌──────┴────────────────┐  ┌───┴──────────────────────────────┐
  │  Workspace = data/    │  │  Analyst (sandboxed subagent)    │
  │  as a git repository  │◄─┤  workspace mounted READ-ONLY     │
  │  plan/ athlete/       │  │  scratch/ for scripts and data   │
  │  activities/ wellness/│  │  Python: pandas, numpy, plotting │
  │  journal/ reports/    │  │  no network (web opt-in per run) │
  │  outbox/ settings     │  │  out: report, charts, proposal   │
  └───────────▲───────────┘  └──────────────────────────────────┘
              │ sync (activities, streams, intervals, wellness, zones)
              ▼ push (planned sessions as calendar events → watch)
  ┌──────────────────────────────────────────────┐
  │  Provider contract (one active provider)     │
  │  intervals.icu (first) · TrainingPeaks ·     │
  │  Strava (read-only) · Runalyze · a local tool│
  └──────────────────────────────────────────────┘
                ←→  Garmin / Coros / Polar / Suunto / …
```

Four parts, each with one job:

- **The surface** shows one stream and one input. It renders cards it is given; it never computes anything.
- **The server** keeps the invariants: the gate, the write boundary, the queue, the renderer, the provider contract and the active provider's connector, the triggers. It is the part that must be right regardless of the model or the provider.
- **The interface agent** talks to the athlete and operates the workspace: it reads, explains, logs, edits what it authors, drafts proposals, and decides when a question needs the analyst.
- **The analyst** runs in a sandbox over a read-only copy of the data, computes, and returns reports, charts and proposals. It never talks to the athlete directly and never writes the workspace.

## The workspace

The workspace is what the agent sees. It is the current `data/` directory, reorganised so that reading it is enough to understand the athlete, and turned into a git repository.

```
data/
  GUIDE.md                   the workspace guide: what every file is, the CLI, the rules (the agent's CLAUDE.md)
  settings.yaml              units, quiet hours, which triggers are on, model and budget, the active provider and its athlete id
  athlete/
    memory.md                the coach's notes, as today: the one file that outlives everything
    profile.yaml             RECORDED: birth year, HR max/rest, zones, thresholds, mirrored from the provider
    gear.yaml                authored: shoes and their mileage, from the provider's gear where it has one, else from conversation
  plan/                      GATED: written only by `openstride plan apply`
    plan.json                the active plan (the current schema: weeks → sessions → steps, dated)
    request.json             what the plan was built from
    paces.json               reference paces, computed in code
    calendar.json            plan session key → the provider's event id, for push and drift checks (empty for a provider with no calendar)
  proposals/
    2026-11-06T09-12_next-2-weeks.json   a proposed plan file plus rationale and changes; pending, approved or rejected
  activities/                RECORDED: written only by the active provider's connector
    2026-11-05T18-02_run.json            one activity in the neutral shape: summary, the provider's analysis under `source`, and which provider it came from
    2026-11-05T18-02_run.streams.json    the recording (time, distance, HR, pace, cadence, altitude), fetched on first need
    index.jsonl                          one line per activity: id, date, type, distance, time, load; the fast way to scan a season
  notes/                     authored: the agents' sidecars to the record, one folder per activity id
    2026-11-05T18-02_run/review.md       the review, as the agent wrote it
    2026-11-05T18-02_run/match.json      which planned session this counts as, when the automatic match is wrong
    2026-11-05T18-02_run/athlete.md      what the athlete said about it (RPE, how it felt), written from conversation
  wellness/                  RECORDED: one line per day from the provider: fitness, fatigue, form, resting HR, HRV, sleep, weight, in the neutral names
    2026-11.jsonl
  journal/
    2026-11-05.md                        what the agent did and concluded that day: the review it wrote, what it asked, what it noticed
    weeks/2026-W45.md                    the weekly review
  reports/                   authored by the analyst: one folder per run, with the report, its charts and the scripts that made them
    2026-11-06T10-40_easy-pace-drift/
  outbox/                                cards the agent asked to show, consumed by the server (see Cards)
  logs/
    usage.jsonl                          tokens and cost per turn
```

What this removes: `plans/<id>/versions/`, `undo_keys`, `chat.json` per plan, `metrics_cache.json`, `reviews/`, `raw/`, `series/`. Versions are git history. The chat is the harness session plus the journal. Metrics come from intervals.icu. Reviews live beside the activity, in `notes/`. Raw files live on intervals.icu.

**The write boundary, by directory.** The labels above are the whole rule:

| Tier | Directories | Who writes | How it is enforced |
|---|---|---|---|
| Recorded | `activities/`, `wellness/`, `athlete/profile.yaml` | The active provider's connector only | Hook denies the agents; read-only mount in the container setup; post-turn check reverts |
| Gated | `plan/` | `openstride plan apply`, with the athlete's approval | The same, plus the token check in `apply` |
| Authored | `athlete/memory.md`, `athlete/gear.yaml`, `notes/`, `journal/`, `reports/`, `proposals/`, `settings.yaml`, `outbox/` | The interface agent directly; the analyst only through `reports/` and `plan draft` | Git: every write is a commit with a reason, and a revert away |

A wrong fact in the record is fixed at the source, on the provider, and comes back on the next sync. A disagreement with the record, say a run the athlete says was a race, is a sidecar in `notes/`, never an edit to the mirror.

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

**The provider** (the only network the agent has; the commands are the same whichever provider is active):

- `openstride source sync [--since …]` : pull activities, wellness and athlete settings into the mirror; prints what is new
- `openstride source streams <activity-id>` : fetch and cache one activity's recording
- `openstride source push [--week N]` : re-send planned sessions as calendar events, where the provider has a calendar (also run by `plan apply`)
- `openstride source upload <file.fit>` : forward a file the athlete dropped in the chat to the provider, then sync, where the provider accepts uploads
- `openstride source wellness --date today --note "…" --soreness 3` : write wellness fields the athlete reports in conversation back to the provider, where it accepts them
- `openstride source capabilities` : what the active provider can and cannot do, so the agent does not offer what is not there

**The analyst** (the only way to run computation outside the interface agent's own shell):

- `openstride analyse "<question>" [--web] [--effort high]` : starts an analyst run in the sandbox with the question and the thread's relevant context, waits, and prints the report path and a summary. The analyst's charts go through `show chart`, its proposals through `plan draft`, so they arrive in the stream like anything else. The analyst itself is also a plug-in point: the default is a harness session in the sandbox, but a run can be routed to an external analysis service that takes a question and the workspace and returns a report in the same folder shape
- `openstride reports` : what the analyst has produced, newest first

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

**What does not:** the coach's notes, the journal, a review, a match correction, logging what the athlete says about a run, a wellness note sent to the provider, a setting, syncing, re-pushing the calendar, running the analyst, reverting something an agent authored. The agent does these and says so in a line.

**What neither agent can do at all:** change, delete or fabricate anything in the record. A duplicate or a wrong activity is removed on the provider, by the athlete, and disappears on the next sync.

**The flow:**

1. The agent decides a change is warranted (the athlete asked, or a skill concluded it). It reads the plan, writes the new version to a scratch file, runs `plan validate`, fixes errors itself, and calls `plan draft` with a rationale and a per-session summary of changes.
2. The server stores the proposal, mints an approval token bound to it and to the plan's current git commit, and pushes a **proposal card** into the stream: the rationale, the trade-offs the validator warned about, a week-by-week diff, Approve and Reject.
3. The agent's turn ends with its explanation above the card. Nothing has changed yet.
4. The athlete taps Approve. The server calls `plan apply` with the token. The plan is written, committed, pushed to the calendar. The agent gets a short brief ("Proposal X approved and applied; calendar updated") on its next turn. Reject is recorded with an optional reason, which the agent reads next time.

A trivial edit costs the athlete one tap, which is the price of the rule being uniform. An "auto-approve my own explicit edits" setting is possible later, but it would be the first exception to the one rule, so this design leaves it out. See the open questions.

**Enforcement, in layers:**

- The harness's `PreToolUse` hook denies any Bash, Write or Edit call whose arguments touch `plan/` or a recorded directory unless the command is `openstride plan apply`. Hooks run in code, before the tool runs.
- `plan apply` checks the token's signature, the proposal id, and that the plan file's commit is the one the proposal was based on.
- After every turn the server checks `git status` and the hashes of `plan/` and the recorded directories: a change that did not come through `apply` or the connector is reverted and the agent is told, in the next turn, what it tried and why it was undone.
- In the containerised setup (`compose.yaml`), the interface agent runs in its own container with `plan/` and the recorded directories mounted read-only, the analyst in another with the whole workspace read-only, and the server applies the plan and runs the connector in its own. That makes the hook a convenience and the mounts the boundary.

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

**The analyst is a subagent, not a second product.** The harness's subagent mechanism starts it; what makes it the analyst is where it runs and what it is given. See the next section.

**Models and effort.** The conversation runs on Claude Opus 5.5 at high effort, as today's default. Trigger-initiated turns run at medium effort. The analyst runs on the same model as the conversation, because its work is the kind that rewards capability, at an effort the caller chooses; cheap bulk reads inside an analyst run go to Claude Sonnet 5.5 subagents of its own. All of these are settings the athlete can change by asking, and a stronger model is a one-line change, which is the point.

## The analyst

The interface agent is good at the everyday: reading the last run, explaining a chart, moving a session. It is the wrong tool for "is my easy pace drifting faster through this block", "build me a marathon plan from everything you know about me", or "which of my shoes is done". Those need code over many files, intermediate data that should not pass through a conversation's context, and a place to be wrong without consequence. That is the analyst.

**Where it runs.** A sandbox: a container (or, bare, a locked-down subprocess) with the workspace mounted read-only at the same path, a `scratch/` directory it owns, Python with pandas, numpy, scipy and a plotting library, the `openstride` CLI with only `paces`, `dates`, `plan validate`, `plan diff`, `plan draft`, `show` and `source streams` available, and no network. Web search and fetch are a per-run option (`--web`) for research that needs it, such as shoe reviews or a race course profile, and off otherwise, because the web is the one way text nobody vetted reaches the sandbox.

**What it is given.** The question, the relevant part of the thread as the interface agent summarises it, and the workspace. It reads `GUIDE.md` like the interface agent does, plus its own skill, `analyst`, which says how to work: load the index and the streams it needs, compute with code, check the result two ways, keep every script and the data it derived in the report folder so the work can be rerun, and write a report a runner can read with the numbers that matter and a chart where one helps.

**What it returns.** Only authored things:

- a report folder under `reports/`, with the Markdown report, the charts, the scripts and the derived data
- cards, through `show chart` and `show table`, which the server renders into the stream under the interface agent's reply
- a proposal, through `plan draft`, when the question was about the plan or the findings warrant one, which lands as the same approval card as any other
- additions to `athlete/gear.yaml` or a suggested note for `memory.md`, handed back to the interface agent to write, since the analyst does not write the workspace

The interface agent reads the summary, tells the athlete what was found in its own words, and shows the cards. The athlete can ask to see the report, the scripts or the data behind any number.

**When it runs.**

- On demand, when the interface agent judges a question needs computation. The `GUIDE.md` rule of thumb: more than a handful of activities, any trend, any plan from scratch, anything with a number the athlete will act on.
- On a schedule, from the triggers: the weekly review calls it for the planned-against-done numbers; a monthly block review and the race report are analyst runs end to end.
- Never on its own initiative, and never talking to the athlete directly.

**Examples of what it can do that the interface agent should not attempt.** Pace at a fixed heart rate across the block, corrected for temperature and climb from the streams. The decoupling of HR from pace in long runs, as a trend. Time in zones against the plan's intent, week by week. Which shoe the easy miles went on and how many are on each pair, against the retirement range the athlete set. A full plan drafted from the request, the paces, the history and the notes, validated, with its rationale, as a proposal. A block review that says what worked and what to change, with the numbers behind it.

**Why a separate agent and not a bigger tool list.** Sandboxing is what lets the analyst be given a shell, Python and the whole dataset without any risk to the record or the plan. Separation is what keeps the conversation's context clean and lets the analyst take ten minutes and many tool calls while the interface agent stays responsive. And the boundary makes the analyst swappable: a better model, a different harness, or a hosted sandbox later, with the same inputs and outputs.

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

## Providers: OpenStride as a framework

OpenStride does not analyse recorded data. A *provider* does: a service or tool that takes what the watch recorded, computes the analysis a coach needs, and exposes it over an API. OpenStride defines the contract, keeps the mirror in a neutral shape, and lets the athlete choose the provider. The provider is the system of record for everything the athlete *did* and *measured*. OpenStride remains the system of record for the *plan* and the *coaching*.

**The contract.** A provider is a Python module behind one interface, with a declared set of capabilities. Required:

| Capability | What it must return | Lands in |
|---|---|---|
| `activities(since)` | Each activity's summary (date, sport, distance, time, HR, pace) and the provider's analysis of it (load, intensity, zone times, detected intervals, whatever it computes), in the neutral shape with the provider's own fields under `source` | `activities/` |
| `streams(id)` | The recording as columns: time, distance, HR, pace, cadence, altitude, plus any the provider adds | `activities/*.streams.json` |
| `wellness(since)` | Per day: fitness, fatigue, form, resting HR, HRV, sleep, weight, under neutral names, with the provider's own model named in `source` so the agent knows whose fitness number it is reading | `wellness/` |
| `athlete()` | Zones, thresholds, HR max and rest | `athlete/profile.yaml` |

Optional, and declared so the agent and the CLI know what is there:

| Capability | What it does |
|---|---|
| `calendar` | Planned sessions become events the provider delivers to the watch; `plan apply` pushes, sync checks for drift |
| `upload` | A file dropped in the chat is forwarded to the provider and comes back through sync |
| `wellness_write` | What the athlete reports in conversation is written back |
| `gear` | Shoes and mileage, into `athlete/gear.yaml` |
| `events` | The provider notifies instead of being polled |

The neutral shape is deliberately thin: the fields every provider has, named once. Everything else goes under `source` untouched, and `GUIDE.md` carries a short page per provider describing those fields and units, so the agents read a Garmin-via-intervals.icu activity and a TrainingPeaks one with equal confidence. Nothing in the agents, the skills, the cards or the plan refers to a provider by name.

**One active provider** holds the record. A second, read-only one can be mirrored beside it for history (an old Strava account, say), marked as such, without being the system of record. Switching providers is a re-sync into an empty mirror: the record is the provider's, the authored data is OpenStride's and stays.

**Candidates.** intervals.icu first: all four required capabilities, calendar, upload, wellness write and gear, a free API for one's own data, and it covers most watches. TrainingPeaks: activities, streams, wellness and a calendar, behind a partner API that needs an agreement. Strava: activities and streams, no planned-workout delivery, a reasonable read-only second source. Runalyze: a free API with its own analysis. A local tool such as a GoldenCheetah export or OpenStride's own current parsers can be a provider that reads files from a folder and computes a minimal analysis, for an athlete who wants nothing in the cloud; it declares no calendar and the agent offers `.fit` downloads as today.

**What OpenStride stops doing:** parsing `.fit` and `.gpx` in the core, computing TRIMP, CTL, ATL and TSB in the core, matching by its own rules alone (the provider pairs an activity with the planned event on the same day where it has a calendar; the agent handles the exceptions and sets the match in `notes/`), talking to Garmin directly. The current parsers and metrics survive only as the local provider.

**Sync** is the same for every provider: a timer (every 15 minutes while the server runs, and on demand), a `since` floor with a few days of overlap for late uploads, the neutral shape written into the mirror, and a trigger per new activity. A provider with `events` replaces the timer.

**Drift,** for a provider with a calendar: if the athlete edits or deletes a planned event there, the next sync notices and briefs the agent, which asks whether to make the plan match (a proposal) or re-push. OpenStride never silently adopts a change to the plan from outside.

**Setup** is the provider's credentials, asked for in the first conversation and stored in `data/secrets/`, where the Garmin token lives today. They never enter the workspace the agents read; the CLI reads them from the server's environment.

### intervals.icu, the first provider

- **In:** activities with intervals.icu's analysis (training load, intensity, pace and HR zone times, detected intervals, efficiency, the device's extras), streams on first need, wellness with intervals.icu's fitness, fatigue, form and ramp rate plus resting HR, HRV, sleep and weight, and the athlete's zones and thresholds.
- **Out:** on every `plan apply`, the changed sessions become structured workout events on the intervals.icu calendar, with the event id kept in `plan/calendar.json`; intervals.icu delivers them to Garmin, Coros, Wahoo and the rest. This replaces the Garmin modules, the unofficial login, the `.fit` encoder and the experimental push in one move, and covers more watches than Garmin.
- **Uploads and wellness write** both exist, so a `.fit` dropped in the chat and a soreness note from conversation both go up and come back through sync.
- **Credentials:** the athlete id and an API key.

## When the agent acts on its own

Omarchy briefs the agent when the system does something. OpenStride briefs it when the athlete does. Each trigger is a short message queued on the same session, so the agent handles it with the full thread in mind, and the result appears in the stream like any reply.

| Trigger | When | What the agent does, by its skill |
|---|---|---|
| New activity | A sync finds one, from whichever provider is active | Reviews it against the planned session, writes the review on the activity and in the journal, matches it if the automatic match looks wrong, asks one question if something is unclear, drafts a proposal only if a trend warrants one |
| Morning brief | A time the athlete sets; off by default | Two lines: today's session in context, one thing worth knowing from wellness |
| Wellness signal | A sync shows an anomaly intervals.icu flags, or a sharp drop in HRV, sleep or form against the athlete's own baseline | Says so, asks how they feel, may propose an easier day |
| Week end | The last planned day of the week has passed | The weekly review: planned against done, load against form, what the next week needs; proposals if so. The numbers come from an analyst run |
| Block review | Every four weeks of a plan; off by default | An analyst run over the block: trends, what worked, what to change, as a report and possibly a proposal |
| Plan ends | The day after the race | The race report, an analyst run, and the plan-history entry in memory; asks what is next |
| Calendar drift | A sync finds a planned event changed on the provider | Asks what the athlete wants |
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

- **The API keys** (Anthropic, the provider's) stay in the server's environment and `data/secrets/`, outside the workspace. The agent's shell never sees them; the CLI's `source` commands run through the server.
- **The shells** are confined to the workspace directory and have no network except through `openstride source`, and for the analyst only when a run opts into the web. In the container setup that is a container boundary; bare, it is the hook denying paths outside the workspace plus a user-level sandbox where the platform offers one. This is the athlete's own data on the athlete's own machine, so the threat is a mistake, not an attacker, but the plan, the record and the secrets are protected as if it were.
- **Injection** through data: activity names and descriptions arrive from the provider and can contain anything, and a web-enabled analyst run reads pages nobody vetted. The guide says both are data. The only consequential action, a plan change, waits for the athlete anyway; the record cannot be written by any agent; and everything authored is in git.
- **Password and network exposure** stay as today: localhost or Tailscale, a password beyond that, refusal to start open without one.

## What stays, what goes

| Module today | Fate |
|---|---|
| `vdot.py`, `validate.py`, `models.py` (plan schema), `clock.py` | Stay, behind `openstride paces`, `plan validate`, `dates` |
| `adapt.merge`, `adapt.check`, the diff rendering | Stay, behind `plan draft` and `plan diff` |
| `lifecycle.py`, `storage.py` versions and undo | Replaced by git and `plan apply` |
| `edits.py` | Its rules (no two runs a day, nothing into the past, race fixed) move into the validator; the operations become the agent editing the plan file |
| `chat.py`, `prompts/*` | Replaced by the harness, `GUIDE.md` and the skills |
| `review.py`, `generator.py`, `adapt.create_proposal` | Become skills; the model writes the plan file directly and the CLI validates it. Plan generation is an analyst run |
| `activities.py`, `series.py`, `metrics.py` | Leave the core; they can become the local provider for an athlete who wants no cloud service |
| `workout_fit.py`, `garmin/*` | Removed; a provider with a calendar delivers workouts. The `.fit` encoder may return as the local provider's download |
| `memory.py` | Stays as a file convention; git replaces its versioning |
| `static/` | Rewritten as the stream and the card renderer; `charts.js` survives inside the activity and fitness cards |
| Tests | The fact modules keep theirs. New tests: the gate and the write boundary (every way around them), the card renderer, the provider contract against a fake provider that every real one must also pass, the intervals.icu connector against a recorded API, the analyst sandbox (no write, no network, no secrets), the triggers, and the skills against the demo workspace with a recorded model |
| The offline demo | Stays, as a demo workspace with a recorded session, and is how the surface is developed without an API key |

## Phases

Each phase ships on its own and leaves the current app working until the last one.

1. **The provider contract, with intervals.icu as the first provider, Garmin out.** The interface, the neutral shape, the mirror layout, the capability flags, the intervals.icu connector, the calendar push. The existing coach reads the mirror instead of its own parsers. Deletes the most code for the least risk, and decides the data layout the rest depends on. A second provider, even a trivial local one, is written in this phase too, because a contract with one implementation is not a contract.
2. **The workspace and the CLI.** `data/` becomes a git repository with the layout above; the CLI exposes the fact modules and the gated `plan draft`/`plan apply`. Nothing user-facing changes yet; the current routes call the CLI.
3. **The interface agent behind the Coach tab.** The harness replaces `chat.py`, with the guide, the hooks, the write boundary and the first skills (`review-run`). The approval card appears under the coach's replies, as proposals do today. The rest of the app is still there for anything the agent does not yet do well, which is how the skills get tuned.
4. **The analyst.** The sandbox, `openstride analyse`, the `analyst` skill, and `new-plan` as its first job, replacing `generator.py`. Then the weekly review's numbers.
5. **The stream.** The new surface replaces the tabs: glance header, stream, cards, input, Data drawer. The old pages are removed as each becomes a card.
6. **Triggers.** New activity, week end, plan end first; the block review, the morning brief and wellness signals once the first three feel right.
7. **Harden.** The container split with the read-only mounts, the budget cap, the hosted-version questions.

## Open questions

- **Approving trivial edits.** Uniform rule, one tap each, or an exception for the athlete's own explicit edits? This design keeps the rule.
- **The Node runtime.** Is a dependency on the Claude Code harness acceptable for the bare install, or should the fallback harness be the default from the start?
- **How much to show without asking.** The glance header is the minimum. A tap on "this week" could open a week card without a message. That is still the agent's data and renderer; it is only a question of whether a tap counts as a message.
- **File uploads.** Keep the drop-a-`.fit` path (forwarded to intervals.icu) or require every activity to come through intervals.icu?
- **Hosted later.** The workspace and CLI design ports to a managed sandbox per athlete without change, and the analyst is the natural first thing to host. Whether that is the hosted version, and what it would cost per athlete, is a separate decision.
- **The analyst's web access.** Off by default, on per run. Should some runs, such as a shoe question, turn it on without asking, or should the athlete always be told first?
- **The neutral shape.** How thin to keep it. Every field promoted out of `source` is one every provider must supply or leave empty; every field left in `source` is one the agent must learn per provider from the guide. The draft leans thin.
- **The local provider.** Whether to keep the current parsers alive as one from the start, or add it later when someone asks for a no-cloud setup.
