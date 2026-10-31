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

## Garmin (experimental: not yet tested against a real Garmin account)

Runs can arrive from Garmin Connect, and planned sessions can go to your watch. Set `GARMIN_MODE` in `.env`, then connect once from a terminal:

    uv run python -m app.garmin.login

| `GARMIN_MODE` | Sync runs | Send workouts | How it connects |
|---|---|---|---|
| `off` (default) | no | no | Download workout .fit still works |
| `personal_api` | yes | no | Garmin's official, read-only Personal API |
| `unofficial` | yes | yes | `python-garminconnect`, with your Garmin login |

**`personal_api`.** In Garmin Connect's account settings, create a Personal API key and copy it, since it is shown only once. Paste it at the login prompt. It is saved to `data/secrets/` with mode 600. Keys expire after 30 days to a year; when yours does, create a new one and run the login again. Garmin says this API is for your own data and not for commercial apps serving several users, and it cannot send workouts.

**`unofficial`.** **Read this first.** This mode uses `python-garminconnect`, which logs in the way Garmin's mobile app does. Garmin does not sanction it, it can break without notice whenever Garmin changes its login or endpoints, and it sits in a grey area under Garmin's terms. The login asks for your email, password and MFA code in the terminal. The password is used once and never stored. Only the tokens are, in `data/secrets/garmin_tokens.json` with mode 600. They can reach your account, so treat that file like a password; deleting it does not revoke them at Garmin. A rejected token needs the login again.

**Syncing.** Press **Sync from Garmin**, or set `GARMIN_SYNC_MINUTES` to sync on a timer while the server runs (0 is manual only, minimum 15). Errors show in the status and never stop the server. Runs and other sports go through the same steps as an uploaded file: matching, review, charts and metrics. Strength sessions become logged sessions with no route. The first sync starts a week before the active plan, or 30 days back with no plan. Later syncs look back 3 days from the last one, so a run the watch uploads up to 3 days late is still found; anything later has to be uploaded by hand. If a review fails, for example on a bad Claude key, the run is still imported, the sync reports a warning, and you can re-run the review. An activity is imported once: if you delete a workout here, sync won't bring it back. If a file has nothing to read, the workout is built from Garmin's summary and marked partial, with no route or charts.

**Workouts on the watch.** Every planned run has **Download workout .fit**. Connect the watch by USB and copy the file into `GARMIN/NewFiles`; many Garmin watches pick it up on the next unplug, so test yours. In `unofficial` mode, **Send to Garmin** uploads the workout, schedules it on the session date and sends it to your last-used device, and it also reaches the watch at its next sync if that send fails. Sending a session again replaces its old Garmin workout. After a plan change, the weeks page offers to re-send only what changed. Past and completed sessions are not sent. Strength and cross sessions are not exported.

**Secrets** live only in `data/secrets/`. They are never logged, sent to Claude, or written to plan or activity files.

## Password

Set `OPENSTRIDE_PASSWORD` for any setup other than localhost or Tailscale. Every page and API route then needs a login, valid for 30 days on that device. Changing the password signs every device out.

The server refuses to listen beyond localhost with no password, because it holds your API key and an open server can spend your money. `OPENSTRIDE_ALLOW_OPEN=1` overrides that; `compose.yaml` sets it because the container must listen on all its interfaces while the only published port is on the host's localhost. If you change `ports:` to publish wider, set a password.

## Backups

`GET /api/backup` downloads `openstride-backup-<date>.zip`: the data folder without lock files, `data/secrets/` and earlier backups. Add `?include_secrets=1` to include secrets, which are tokens, so keep that file private.

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
