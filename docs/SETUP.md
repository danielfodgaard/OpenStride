# OpenStride setup

How to reach OpenStride from your phone, connect Garmin, and keep it safe. For the basic install, see the [README](../README.md).

## Phone access

Phone and laptop use one server over HTTPS. Nothing syncs: both talk to the same OpenStride, and the laptop must be on and running it. The recommended way is Tailscale, which needs no public port and gives a valid certificate:

1. Install Tailscale on the laptop and the phone, and log in with the same account on both.
2. Once per tailnet: in the [admin console](https://login.tailscale.com/admin/dns), enable MagicDNS and HTTPS Certificates. The script tells you if this is missing.
3. Once per machine, so the script doesn't need root: `sudo tailscale set --operator=$USER`.
4. Start OpenStride, then run `scripts/phone-access.sh` (Windows: `powershell -File scripts\phone-access.ps1`). If you changed the port, set `OPENSTRIDE_PORT` for the script too.
5. Open the printed `https://<machine>.<tailnet>.ts.net` URL on the phone and use Add to Home Screen.

The server stays bound to localhost; only devices in your tailnet can reach it. `scripts/phone-access-off.sh` (or `.ps1`) turns it off, and clears any other `tailscale serve` setting on the machine too. No password is needed unless you share the tailnet with other people.

On a server without Tailscale installed, uncomment the `tailscale` service in `compose.yaml`, follow the notes there, and put `TS_AUTHKEY` in `.env`.

On the same Wi-Fi without Tailscale, `OPENSTRIDE_HOST=0.0.0.0` with a password and `http://<laptop-ip>:8000` works too. It has no HTTPS, so the app can't be installed on the phone and the password crosses the network in the clear. Prefer Tailscale.

## Garmin

Reading your activities and sending workouts are two separate settings in `.env`:

| Setting | Values | What it does |
|---|---|---|
| `GARMIN_SYNC` | `off`, `personal_api` | Sync runs through Garmin's official, read-only Personal API |
| `GARMIN_PUSH` | `off`, `unofficial` | Send workouts through `python-garminconnect` |

Download workout .fit works with both off. The older `GARMIN_MODE` still works: `personal_api` turns on sync, and `unofficial` turns on both.

**Syncing runs.**

1. Create a key at <https://connect.garmin.com/app/settings/personalApiToken> and choose 365 days. It is shown once.
2. In `.env`, set `GARMIN_SYNC=personal_api` and `GARMIN_PERSONAL_API_KEY=<your key>`, then restart the server.
3. Press **Sync from Garmin** on the Workouts page, or set `GARMIN_SYNC_MINUTES` to sync on a timer while the server runs (0 is manual only, minimum 15).

When the key expires, sync reports that Garmin refused it: create a new one and replace it in `.env`. The key mints a 24-hour access token, cached in `data/secrets/` with mode 600. Garmin says this API is for your own data and not for commercial apps serving several users. It cannot send workouts.

Runs and other sports go through the same steps as an uploaded file: matching, review, charts and metrics. Strength sessions become logged sessions with no route. The first sync starts a week before the active plan, or 30 days back with no plan. Later syncs look back 3 days from the last one, so a run the watch uploads up to 3 days late is still found; anything later has to be uploaded by hand. If a review fails, the run is still imported and the sync reports a warning. An activity is imported once: if you delete a workout here, sync won't bring it back.

**Workouts on the watch (experimental: not yet tested against a real Garmin watch).** Every planned run has **Download workout .fit**. Connect the watch by USB and copy the file into `GARMIN/NewFiles`; many Garmin watches pick it up on the next unplug. Strength and cross sessions are not exported.

**Send to Garmin (experimental: not yet tested against a real Garmin account).** The Personal API can't write, so sending uses `python-garminconnect`, which Garmin does not sanction and which can break without notice.

1. Set `GARMIN_PUSH=unofficial` in `.env` and restart.
2. Open **Garmin settings** (in the footer) at `http://127.0.0.1:8000` or the Tailscale HTTPS address, and log in, with your MFA code if asked. The login is refused anywhere else.
3. **Send to Garmin** on a planned run uploads the workout, schedules it on the session's date and sends it to your last-used device. Sending it again replaces the old copy. After a plan change, the weeks page offers to re-send only what changed.

**Secrets** live only in `.env` and `data/secrets/`. They are never logged, sent to Claude, or written to plan or activity files.

## Garmin security

The login for sending saves a session token that gives **full access to your Garmin account**, so treat it like a password.

- **What it does here:** only uploads, schedules, replaces and deletes workouts OpenStride made. Your activities are read through the official API, never with this token.
- **Where it lives:** `data/secrets/garmin-unofficial/`, folder mode 700 and files 600. Your password is never written anywhere. The token is left out of every backup, even with "include secrets".
- **Disconnect:** press **Disconnect Garmin** in Garmin settings, or run `uv run openstride garmin-disconnect`. Either one deletes the token and turns sending off.
- **Revoking access:** deleting the token here doesn't end it at Garmin. If it may have leaked, change your Garmin password too. Keep MFA on.
- **Exposure:** sending is refused if the server listens beyond localhost with no `OPENSTRIDE_PASSWORD`. Never use `tailscale funnel`: it would put this server on the public internet.
- **Upgrades:** `python-garminconnect` and its dependencies are pinned to exact versions in `pyproject.toml` and `uv.lock`. Upgrade them deliberately and read what changed first.

## Password

Set `OPENSTRIDE_PASSWORD` for any setup other than localhost or Tailscale. Every page and API route then needs a login, valid for 30 days on that device. Changing the password signs every device out.

The server refuses to listen beyond localhost with no password, because it holds your API key and an open server can spend your money. `OPENSTRIDE_ALLOW_OPEN=1` overrides that; `compose.yaml` sets it because the container must listen on all its interfaces while the only published port is on the host's localhost. If you change `ports:` to publish wider, set a password.

## Backups

`GET /api/backup` downloads `openstride-backup-<date>.zip`: the data folder without lock files, `data/secrets/` and earlier backups. Add `?include_secrets=1` to include secrets, which are tokens, so keep that file private. The Garmin login token is never included.

`scripts/backup.sh <folder>` writes the same zip to a folder, and is safe while the server runs. For a daily backup at 03:00, in `crontab -e`:

    0 3 * * * PATH=$HOME/.local/bin:$PATH /path/to/OpenStride/scripts/backup.sh $HOME/openstride-backups

To restore, stop the server and unzip into an empty data folder. A backup without secrets means logging in again. Migrations write a backup to `data/backups/` first.

With Docker: `docker compose exec openstride openstride backup /app/data/backups`.

## Running on an always-on server

The laptop can be replaced by a small VPS or home server, using the same `compose.yaml` and `.env`.

- **Through Tailscale (recommended):** no public port. Install Tailscale on the server and run `scripts/phone-access.sh`, or use the sidecar in `compose.yaml`.
- **Behind Caddy:** automatic HTTPS on a domain you own. Set `OPENSTRIDE_PASSWORD` in `.env`, keep the port on `127.0.0.1` as `compose.yaml` has it, install Caddy on the server, and use this `Caddyfile`:

      openstride.example.com {
          reverse_proxy 127.0.0.1:8000
      }

  This one is on the public internet, so the password is not optional, and pick a long one.
