# OpenStride setup

How to connect your activities, reach OpenStride from your phone, and keep it safe. For the basic install, see the [README](../README.md).

## The provider: intervals.icu

OpenStride does not read your watch. A provider does, analyses the recordings, and OpenStride mirrors the result. The first provider is [intervals.icu](https://intervals.icu), which is free for your own data.

1. Create an intervals.icu account and connect your watch platform to it **directly** under Settings (Garmin, Coros, Polar, Suunto, Wahoo). Do not route it through Strava: Strava does not let intervals.icu pass its data on through the API, and those activities arrive empty.
2. Under Settings, Developer Settings, copy your **API key** and your **athlete id** (it looks like `i123456`).
3. Put both in `.env`:

       INTERVALS_ICU_API_KEY=...
       INTERVALS_ICU_ATHLETE_ID=i123456

4. Check it: `uv run openstride doctor`. It says who you are connected as. `uv run openstride doctor --write` also creates one test workout a year from now on your calendar, reports whether intervals.icu read it as a structured workout, and removes it.
5. Start OpenStride. The first sync brings the last 180 days; after that it polls every 15 minutes (`sync_minutes` in settings) and on **Sync now** in the Data drawer. For more history, ask your coach to sync from an earlier date.

**What flows where.** Activities, their analysis, wellness and your zones come in and are kept under `data/workspace/activities/`, `wellness/` and `athlete/profile.yaml`. When you approve a plan change, the sessions still ahead go out to your intervals.icu calendar as structured workouts, and intervals.icu sends them to your watch if you turned that on there. A session with no recording that you tell your coach about is logged on intervals.icu as a manual activity; how you feel is written to its wellness page.

**If you edit a planned workout on intervals.icu,** the next sync notices and your coach asks what you want: make the plan match (a proposal) or put the calendar back. OpenStride never adopts a change to the plan from outside by itself.

**The key** lives only in `.env`. It is never written into the workspace, never shown to the agent's shell, and never sent to Claude.

**Another analyzer.** The contract is `app/providers/base.py`; `app/providers/demo.py` is a small complete implementation to copy from. A provider that has no calendar simply does not offer watch delivery.

## The sandbox

The agent has a shell. Three things keep it inside the lines: a check before each tool call, an OS sandbox around the shell, and a check after each turn that puts back anything protected that changed. The first and third always run. The second depends on the machine:

| Machine | What it needs |
|---|---|
| macOS | Nothing; Seatbelt is built in |
| Linux | `bubblewrap` and `socat` (`sudo apt install bubblewrap socat`) |
| Docker | The container must be allowed to make namespaces: uncomment `cap_add` and `security_opt` in `compose.yaml`, or set `OPENSTRIDE_SANDBOX=weak` for the harness's weaker nested sandbox. Neither has been tested by me; `doctor` tells you the result |
| Windows | Run under WSL |

`uv run openstride doctor` says whether it is on, and the Data drawer shows it. With it on, the agent's shell can write only what the agent authors, cannot read the server's state or `.env`, and has no network. With it off, a script the agent runs in its shell is not confined by the OS: the plan and the record are still restored after the turn if touched, and commands that name the server, the environment or the network are refused, but a script written to get around that could reach your API key in the shell's environment, the network, and the server's own approve route. A model following its instructions does none of this; the sandbox is what makes it impossible rather than unlikely. On a machine you care about, install the two packages.

`OPENSTRIDE_SANDBOX` in `.env`: `auto` (the default: on when the machine can), `on` (refuse to run a shell without it), `weak`, or `off`.

## Phone access

Phone and laptop use one server over HTTPS. Nothing syncs: both talk to the same OpenStride, and the laptop must be on and running it. The recommended way is Tailscale, which needs no public port and gives a valid certificate:

1. Install Tailscale on the laptop and the phone, and log in with the same account on both.
2. Once per tailnet: in the [admin console](https://login.tailscale.com/admin/dns), enable MagicDNS and HTTPS Certificates. The script tells you if this is missing.
3. Once per machine, so the script doesn't need root: `sudo tailscale set --operator=$USER`.
4. Start OpenStride, then run `scripts/phone-access.sh` (Windows: `powershell -File scripts\phone-access.ps1`). If you changed the port, set `OPENSTRIDE_PORT` for the script too.
5. Open the printed `https://<machine>.<tailnet>.ts.net` URL on the phone and use Add to Home Screen.

The server stays bound to localhost; only devices in your tailnet can reach it. `scripts/phone-access-off.sh` (or `.ps1`) turns it off, and clears any other `tailscale serve` setting on the machine too. No password is needed unless you share the tailnet with other people. Never use `tailscale funnel`: it would put this server on the public internet.

On a server without Tailscale installed, uncomment the `tailscale` service in `compose.yaml`, follow the notes there, and put `TS_AUTHKEY` in `.env`.

The glance header, this week and the cards you opened stay readable offline. A message waits for the server.

## Password

Set `OPENSTRIDE_PASSWORD` for any setup other than localhost or Tailscale. Every page and API route then needs a login, valid for 30 days on that device. Changing the password signs every device out.

The server refuses to listen beyond localhost with no password, because it holds your API keys and an open server can spend your money and approve changes to your plan. `OPENSTRIDE_ALLOW_OPEN=1` overrides that; `compose.yaml` sets it because the container must listen on all its interfaces while the only published port is on the host's localhost. If you change `ports:` to publish wider, set a password.

## Settings you change by asking

`data/workspace/settings.yaml` holds what is yours to tune, and your coach changes it when you say so ("turn on the morning brief at 6:45", "no messages after nine", "cap the month at $10"):

| Setting | Default | |
|---|---|---|
| `triggers.new_activity`, `week_end`, `plan_end`, `calendar_drift` | on | A review when a run arrives, a look back when a week ends, a report after the race, a question when the calendar was edited |
| `triggers.morning_brief`, `morning_brief_time` | off, 07:30 | Two or three sentences on the day |
| `triggers.wellness_signal`, `block_review` | off | A question when resting HR, HRV, sleep or form departs from your own baseline; a computed review every four weeks |
| `quiet_hours` | 21:30 to 07:00 | Everything but a new activity waits for morning |
| `budget.monthly_usd` | 0 (no cap) | The agent's own check-ins pause when the month reaches it; it still answers you |
| `models`, `effort` | Claude Opus 5.5 for the coach and the analyst | A stronger or cheaper model is one line |
| `sync_minutes` | 15 | 0 turns the timer off |

## Backups

`GET /api/backup`, or **Download backup** in the Data drawer, gives `openstride-backup-<date>.zip`: the data folder without secrets, locks and the analyst's scratch folders. `scripts/backup.sh <folder>` writes the same zip and is safe while the server runs. For a daily backup at 03:00, in `crontab -e`:

    0 3 * * * PATH=$HOME/.local/bin:$PATH /path/to/OpenStride/scripts/backup.sh $HOME/openstride-backups

To restore, stop the server and unzip into an empty data folder. The workspace is also a git repository: `git -C data/workspace log` is everything that ever changed, with the reason.

With Docker: `docker compose exec openstride openstride backup /app/data/backups`.

## Coming from 0.1

The new version keeps its data in new folders beside the old ones: `data/workspace/`, `data/state/` and `data/analyst/`. Your old files (`data/plans/`, `data/activities/`, `data/reviews/`, `data/athlete/`) are only ever read, never changed or removed.

The first time the new version starts on an old data folder, it brings over by itself:

- **The plan**, with every earlier version as a commit in the workspace's history.
- **The coach's notes** about you.
- **Reviews, your notes on runs, hand-logged sessions and the chat**, as text under `journal/legacy/`, where the coach can read them.

Activities themselves now come from the provider. If your watch history is already on intervals.icu, the first sync has it. If it is not, `uv run openstride migrate --upload` (with Docker: `docker compose exec openstride openstride migrate --upload`) also sends the raw `.fit` and `.gpx` files OpenStride kept, and your hand-logged sessions, to intervals.icu.

The plan is not sent to your intervals.icu calendar by the migration. Ask your coach to send it ("put my plan on my watch"), or it goes with your first approved change.

`openstride migrate` runs the same import by hand; `--force` replaces a plan the workspace already has, and running it twice changes nothing. To go back to 0.1, check out the old version: its files are where it left them. Garmin settings in `.env` (`GARMIN_*`) are no longer used; intervals.icu takes that role.

## Docker

    docker compose up -d --build
    docker compose exec openstride openstride doctor

The image has git, bubblewrap, socat and the analyst's Python packages. Read the sandbox section above: in a default container the sandbox is off unless you allow namespaces or set `OPENSTRIDE_SANDBOX=weak`. The image has not been run by me; treat the first start as a test.
