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

## Install and phone access

### Install and run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

    cp .env.example .env   # then set ANTHROPIC_API_KEY
    uv run openstride

Open http://127.0.0.1:8000. `uv run` creates the environment from `pyproject.toml`, the only list of dependencies. Data lives in `data/`, including every uploaded file in `data/activities/raw/`. Settings are in `.env`: `OPENSTRIDE_HOST`, `OPENSTRIDE_PORT`, `OPENSTRIDE_DATA_DIR`, `OPENSTRIDE_PASSWORD` and `OPENSTRIDE_ALLOW_OPEN`.

Or with Docker, which keeps the same `./data` folder and restarts by itself:

    cp .env.example .env   # then set ANTHROPIC_API_KEY
    docker compose up -d

`GET /api/health` returns the status, the version and whether the data folder is writable.

### Phone access

Phone and laptop use one server over HTTPS. Nothing syncs: both talk to the same OpenStride, and the laptop must be on and running it. The recommended way is Tailscale, which needs no public port and gives a valid certificate:

1. Install Tailscale on the laptop and the phone, and log in with the same account on both.
2. Once per tailnet: in the [admin console](https://login.tailscale.com/admin/dns), enable MagicDNS and HTTPS Certificates. The script tells you if this is missing.
3. Start OpenStride, then run `scripts/phone-access.sh` (Windows: `powershell -File scripts\phone-access.ps1`). If you changed the port, set `OPENSTRIDE_PORT` for the script too.
4. Open the printed `https://<machine>.<tailnet>.ts.net` URL on the phone and use Add to Home Screen.

The server stays bound to localhost; only devices in your tailnet can reach it. `scripts/phone-access-off.sh` (or `.ps1`) turns it off, and clears any other `tailscale serve` setting on the machine too. No password is needed unless you share the tailnet with other people.

On a server without Tailscale installed, uncomment the `tailscale` service in `compose.yaml`, follow the notes there, and put `TS_AUTHKEY` in `.env`.

On the same Wi-Fi without Tailscale, `OPENSTRIDE_HOST=0.0.0.0` with a password and `http://<laptop-ip>:8000` works too. It has no HTTPS, so the app can't be installed on the phone and the password crosses the network in the clear. Prefer Tailscale.

### Password

Set `OPENSTRIDE_PASSWORD` for any setup other than localhost or Tailscale. Every page and API route then needs a login, valid for 30 days on that device. Changing the password signs every device out.

The server refuses to listen beyond localhost with no password, because it holds your API key and an open server can spend your money. `OPENSTRIDE_ALLOW_OPEN=1` overrides that; `compose.yaml` sets it because the container must listen on all its interfaces while the only published port is on the host's localhost. If you change `ports:` to publish wider, set a password.

### Two devices at once

Writes are atomic and locked, so phone and laptop can act at the same moment. If a plan changed on the other device after you loaded it, the action is refused with a reload rather than overwriting.

### Backups

`GET /api/backup` downloads `openstride-backup-<date>.zip`: the data folder without lock files, `data/secrets/` and earlier backups. Add `?include_secrets=1` to include secrets, which are tokens, so keep that file private.

`scripts/backup.sh <folder>` writes the same zip to a folder, and is safe while the server runs. For a daily backup at 03:00, in `crontab -e`:

    0 3 * * * PATH=$HOME/.local/bin:$PATH /path/to/OpenStride/scripts/backup.sh $HOME/openstride-backups

To restore, stop the server and unzip into an empty data folder. A backup without secrets means logging in again. Migrations write a backup to `data/backups/` first.

With Docker: `docker compose exec openstride openstride backup /app/data/backups`.

### Running on an always-on server

The laptop can be replaced by a small VPS or home server, using the same `compose.yaml` and `.env`.

- **Through Tailscale (recommended):** no public port. Install Tailscale on the server and run `scripts/phone-access.sh`, or use the sidecar in `compose.yaml`.
- **Behind Caddy:** automatic HTTPS on a domain you own. Set `OPENSTRIDE_PASSWORD` in `.env`, keep the port on `127.0.0.1` as `compose.yaml` has it, install Caddy on the server, and use this `Caddyfile`:

      openstride.example.com {
          reverse_proxy 127.0.0.1:8000
      }

  This one is on the public internet, so the password is not optional, and pick a long one.

## Navigating the app

- **Workouts**: every recorded workout, newest first by month, across all plans. Import files and log sessions here; neither needs an active plan. Without one, workouts are saved as unplanned, and Change match on a workout attaches it to a plan later.
- **All plans**: every plan, the active one first. A plan opens on its overview: status, next session, the fitness and weekly volume charts, coach chat, progress summary and Adapt plan.
- **Weeks**, from the overview: foldable weeks, the current one open by default, with a linked row per session. Pace zones and the plan's summary and warnings are here.
- **Workout detail**: one page for a planned session and what you actually ran: the plan, a summary strip, the review, route, pace and heart-rate charts with a shared hover, and laps, with a Target column when the laps line up with the session's steps.

Pages to screenshot for a walkthrough: the plan overview, an open week, a done steady run (`#/plans/<id>/sessions/1/0`), an interval session with its Target column, Workouts, and a session not yet done.

Workouts from before v3.1 have no stored file, so their detail page asks you to import the file again. Doing that adds the route and charts to the existing workout.

Upgrading from v1: run `uv run python -m app.migrate` once. It moves `plans/*.json` to `data/plans/<id>/plan.json` as drafts.

## Offline demo

No API key or network needed:

    OPENSTRIDE_DEMO=1 uv run openstride

This serves a throwaway copy of `examples/demo/data`, including route and chart data for every file, with the date pinned to Wednesday 25 November 2026: an active half-marathon plan with strength and cycling, three weeks of runs with real reviews, two logged strength sessions, a cycling file, one bad run with a note, the charts, a chat thread with a pending proposal, and a progress summary. Your `data/` is not touched and every change disappears when the server stops.

Offline you can open every page, hover the workout charts, read the thread, accept or reject the proposal, view and restore versions, open the chart tables, log a session, delete a workout, and upload `examples/activities/cross/2026-11-12_pool_swim.fit`, which imports with a "review failed" message. Actions that need Claude show an error; with a key in `.env` they work in the demo too.

## Test

    uv run pytest

## Importing files

`.fit` and `.gpx` activities are summarised in code and matched to the planned session on the same date: running to runs, other sports to cross-training, and a watch's gym file to strength. The summary, not the raw file, is what reaches the LLM. GPX counts as running unless `<type>` says otherwise, including Strava's numeric types.

Limits to know:
- File times are UTC. A `.fit` that records its local time uses that; otherwise, as with `.gpx`, the server's time zone applies, so a run while travelling can land on the wrong day. The match can be changed by hand.
- The same run uploaded as both `.fit` and `.gpx` counts once if the start times are within 60 seconds.
- A multisport file counts as one activity of its longest sport.
- Fitness and fatigue start at plan start, from the seed below, so runs before it don't affect them.

## Metrics

`app/metrics.py` computes everything on the charts: load (Banister TRIMP from heart rate, or duration times a type factor without one), fitness (CTL, 42-day average), fatigue (ATL, 7-day average), form (TSB) and weekly volume. Set max and resting heart rate in the form for accurate load; otherwise 208 − 0.7 × age, or 190, and 55 are used. Fitness starts from `current_weekly_km × 1.1`, a rough prior.

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
