"""intervals.icu as a provider: it analyses what the watch recorded, keeps wellness and fitness, and
sends planned workouts on to Garmin, Coros, Wahoo and the rest.

API: https://intervals.icu/api/v1, HTTP basic auth with the user name API_KEY and the key as password.
The fields read here are looked up by name and everything else is kept under `source`, so a field
intervals.icu adds or renames costs a neutral value, not the sync.
"""
import re
import time
from datetime import date

import httpx

from app import units
from app.providers import base
from app.providers.base import Capabilities, ProviderError

BASE_URL = "https://intervals.icu/api/v1"
TIMEOUT_S = 30
RETRY_AFTER_LIMIT_S = 5

SPORT = {
    "Run": "run", "VirtualRun": "run", "TrailRun": "run",
    "Ride": "ride", "VirtualRide": "ride", "GravelRide": "ride", "MountainBikeRide": "ride", "EBikeRide": "ride",
    "EMountainBikeRide": "ride", "TrackRide": "ride", "Cyclocross": "ride", "Handcycle": "ride", "Velomobile": "ride",
    "Swim": "swim", "OpenWaterSwim": "swim",
    "WeightTraining": "strength",
    "Walk": "walk", "Hike": "hike", "Snowshoe": "hike",
    "Rowing": "row", "VirtualRow": "row", "Canoeing": "row", "Kayaking": "row",
    "NordicSki": "ski", "AlpineSki": "ski", "BackcountrySki": "ski", "RollerSki": "ski", "VirtualSki": "ski",
}
PACED = ("run", "walk", "hike")
CROSS_TYPES = (
    (r"rid|bik|cycl|spin", "Ride"), (r"swim", "Swim"), (r"row", "Rowing"), (r"ski", "NordicSki"),
    (r"walk", "Walk"), (r"hik", "Hike"), (r"yoga", "Yoga"), (r"ellip", "Elliptical"),
)
STREAMS = {
    "time": "time_s", "distance": "distance_m", "heartrate": "hr", "velocity_smooth": "speed_mps", "cadence": "cadence",
    "altitude": "altitude_m", "fixed_altitude": "altitude_m", "temp": "temp_c", "watts": "power_w", "grade_smooth": "grade",
}
WELLNESS_WRITE = {
    "resting_hr": "restingHR", "hrv": "hrv", "weight_kg": "weight", "soreness": "soreness", "tiredness": "fatigue",
    "stress": "stress", "mood": "mood", "motivation": "motivation", "comments": "comments", "sleep_quality": "sleepQuality",
}
PRIVATE = re.compile(r"email|token|secret|password|api_?key", re.I)
STRAVA_NOTE = (
    "Synced from Strava, which does not let intervals.icu pass its data on through the API. "
    "Connect the watch to intervals.icu directly (Garmin, Coros, Polar, Suunto, Wahoo) to get the full record."
)

GUIDE = """### intervals.icu

The record comes from intervals.icu. In an activity, `load` is intervals.icu's training load and `intensity`
its intensity (percent of threshold). Under `source` you find the activity as intervals.icu holds it: field
names starting `icu_` are its own analysis (for example `icu_hr_zone_times`, seconds per heart-rate zone
from zone 1; `icu_efficiency_factor`; `decoupling`, percent), speeds are m/s, distances metres, times seconds.
`intervals` are the laps or the work and recovery blocks intervals.icu detected.

In wellness, `fitness` is CTL (42-day load), `fatigue` ATL (7-day), `form` their difference (positive is fresh,
below about -20 is heavy), `ramp` the weekly change in fitness. Subjective fields the athlete can log there use
1 to 4, where 1 is best and 4 worst: `soreness`, `tiredness`, `stress`, and `mood`, `motivation`.
`resting_hr` is beats per minute, `hrv` rMSSD in ms, `sleep_h` hours.

An activity with `partial: true` came through Strava, which blocks its data from the API: say so, and say that
connecting the watch to intervals.icu directly fixes it.

A plan change, once approved, is sent to the intervals.icu calendar as structured workouts, and intervals.icu
passes them to the watch. Paces go as min/km targets. The athlete may also edit an event there; the next sync
reports the difference to you, and you ask what they want."""


def workout_steps(session: dict) -> list:
    """Planned steps as a watch runs them: a step, or (rounds, [steps]) for a repeat block.

    4 reps and 3 jogs are 3 rounds of both and one more rep, not a fourth jog.
    """
    planned = session.get("steps") or []
    if not planned:
        return [{"description": "", "distance_km": session.get("distance_km") or 0, "duration_min": session.get("duration_min") or 0,
                 "target_pace": session.get("target_pace") or ""}]
    items, i = [], 0
    while i < len(planned):
        step = planned[i]
        following = planned[i + 1] if i + 1 < len(planned) else None
        if step["repeat"] > 1 and following and following["repeat"] > 1 and abs(step["repeat"] - following["repeat"]) <= 1:
            rounds = min(step["repeat"], following["repeat"])
            items.append((rounds, [step, following]))
            if step["repeat"] != following["repeat"]:
                items.append(step if step["repeat"] > rounds else following)
            i += 2
            continue
        items.append((step["repeat"], [step]) if step["repeat"] > 1 else step)
        i += 1
    return items


def step_line(step: dict, notes: list[str]) -> str:
    """One line of intervals.icu workout text. `m` means minutes there, so metres are written `mtr`."""
    text = (step.get("description") or "").lower()
    label = "Warm up" if "warm" in text else "Cool down" if "cool" in text else "Recovery" if re.search(r"recover|jog|\brest\b|float", text) else ""
    distance, minutes = step.get("distance_km") or 0, step.get("duration_min") or 0
    if distance:
        amount = f"{distance:g}km" if distance >= 1 else f"{round(distance * 1000)}mtr"
    elif minutes:
        whole, seconds = int(minutes), round((minutes - int(minutes)) * 60)
        amount = f"{whole}m{seconds}s" if seconds else f"{whole}m"
    else:
        amount = "lap press"
    paces = sorted(units.pace_seconds(step.get("target_pace") or ""))
    target = ""
    if paces:
        fmt = lambda s: f"{s // 60}:{s % 60:02d}"
        # Slow to fast, as intervals.icu writes a pace range.
        target = f"{fmt(paces[-1])}-{fmt(paces[0])}/km Pace" if len(paces) > 1 else f"{fmt(paces[0])}/km Pace"
    elif (step.get("target_pace") or "").strip():
        notes.append(f"No pace target on the watch for “{step.get('description') or 'the run'}”: could not read “{step['target_pace']}”.")
    return " ".join(part for part in ("-", label, amount, target) if part)


def workout_text(session: dict) -> tuple[str, list[str]]:
    """A planned run in intervals.icu's workout syntax, which it parses into a structured workout for the watch."""
    notes: list[str] = []
    blocks = []
    for item in workout_steps(session):
        if isinstance(item, tuple):
            rounds, steps = item
            blocks.append("\n".join([f"{rounds}x", *(step_line(step, notes) for step in steps)]))
        else:
            blocks.append(step_line(item, notes))
    # A repeat block needs a blank line before and after it; plain steps may sit together.
    lines, previous_block = [], False
    for block in blocks:
        is_block = "\n" in block
        if lines and (is_block or previous_block):
            lines.append("")
        lines.append(block)
        previous_block = is_block
    return "\n".join(lines), notes


def prose(session: dict) -> str:
    """A session that is not a structured run: what to do, with no line the workout parser could mistake for a step."""
    parts = [session.get("description") or ""]
    for step in session.get("steps") or []:
        amount = f"{step['repeat']} x {step['reps']}" if step.get("reps") else f"{step['repeat']} x" if step.get("repeat", 1) > 1 else ""
        extra = ", ".join(x for x in (step.get("load") or "", f"{step['duration_min']:g} min" if step.get("duration_min") else "", step.get("target_pace") or "") if x)
        parts.append(" ".join(x for x in ("•", step.get("description") or "", amount, f"({extra})" if extra else "") if x))
    return "\n".join(part for part in parts if part)


def event_type(session: dict, context: dict) -> str:
    if session["modality"] == "run":
        return "Run"
    if session["modality"] == "strength":
        return "WeightTraining"
    sport = f"{context.get('cross_training') or ''} {session.get('title') or ''}".lower()
    return next((kind for pattern, kind in CROSS_TYPES if re.search(pattern, sport)), "Workout")


def event_payload(session: dict, context: dict) -> tuple[dict, list[str]]:
    payload = {
        "category": "RACE_A" if session["type"] == "race" else "WORKOUT",
        "start_date_local": f"{session['date']}T00:00:00",
        "name": session["title"],
        "type": event_type(session, context),
        "external_id": f"openstride-{session['id']}",
    }
    notes: list[str] = []
    if session["modality"] == "run" and session["type"] != "race":
        text, notes = workout_text(session)
        payload["description"] = "\n\n".join(part for part in (text, session.get("description") or "") if part)
    else:
        payload["description"] = prose(session)
        if session.get("duration_min"):
            payload["moving_time"] = round(session["duration_min"] * 60)
        if session.get("distance_km"):
            payload["distance"] = round(session["distance_km"] * 1000)
    return payload, notes


class IntervalsIcu:
    name = "intervals.icu"
    capabilities = Capabilities(calendar=True, upload=True, wellness_write=True, manual=True, gear=True)
    guide = GUIDE

    def __init__(self, athlete_id: str, api_key: str, transport: httpx.BaseTransport | None = None):
        self.athlete_id = athlete_id or "0"
        self.api_key = api_key
        self.transport = transport

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=BASE_URL, auth=("API_KEY", self.api_key), timeout=TIMEOUT_S, transport=self.transport)

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self.api_key:
            raise ProviderError("No intervals.icu API key is set. Put INTERVALS_ICU_API_KEY in .env and restart.")
        try:
            with self.client() as client:
                response = client.request(method, path, **kwargs)
                if response.status_code == 429:
                    wait = float(response.headers.get("retry-after") or 1)
                    if wait <= RETRY_AFTER_LIMIT_S:
                        time.sleep(wait)
                        response = client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise ProviderError(f"intervals.icu could not be reached: {type(error).__name__}.") from error
        if response.status_code in (401, 403):
            raise ProviderError("intervals.icu refused the API key or the athlete id. Check both in .env.", response.status_code)
        if response.status_code == 429:
            raise ProviderError("intervals.icu is rate-limiting requests. Try again in a minute.", 429)
        if response.status_code >= 400:
            raise ProviderError(f"intervals.icu answered {response.status_code} for {method} {path}: {response.text[:200]}", response.status_code)
        return response

    def get(self, path: str, **params):
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None}).json()

    @property
    def athlete_path(self) -> str:
        return f"/athlete/{self.athlete_id}"

    def check(self) -> dict:
        raw = self.get(self.athlete_path)
        return {"athlete": raw.get("name") or "", "id": str(raw.get("id") or self.athlete_id)}

    def activities(self, oldest: date, newest: date) -> list[dict]:
        rows = self.get(f"{self.athlete_path}/activities", oldest=oldest.isoformat(), newest=f"{newest.isoformat()}T23:59:59")
        return [self.neutral_activity(raw) for raw in rows if raw.get("id") and raw.get("start_date_local")]

    def neutral_activity(self, raw: dict) -> dict:
        sport = SPORT.get(raw.get("type") or "", "other")
        distance_km = round((raw.get("distance") or 0) / 1000, 3)
        moving = int(raw.get("moving_time") or 0)
        cadence = raw.get("average_cadence") or 0
        if sport == "run" and 0 < cadence < 120:  # one leg's strides; a watch counts both feet
            cadence *= 2
        stub = raw.get("source") == "STRAVA" and not any(raw.get(k) for k in ("distance", "moving_time", "elapsed_time", "average_heartrate"))
        return base.activity(
            id=str(raw["id"]),
            provider=self.name,
            date=raw["start_date_local"][:10],
            start_local=raw["start_date_local"][:19],
            sport=sport,
            name=raw.get("name") or "",
            distance_km=distance_km,
            moving_time_s=moving,
            elapsed_time_s=int(raw.get("elapsed_time") or 0),
            avg_pace=units.pace(raw.get("average_speed")) if sport in PACED else "",
            avg_hr=round(raw.get("average_heartrate") or 0),
            max_hr=round(raw.get("max_heartrate") or 0),
            avg_cadence_spm=round(cadence),
            elevation_gain_m=round(raw.get("total_elevation_gain") or 0, 1),
            load=raw.get("icu_training_load"),
            intensity=raw.get("icu_intensity"),
            rpe=raw.get("icu_rpe") if raw.get("icu_rpe") is not None else raw.get("perceived_exertion"),
            planned_event_id=str(raw.get("paired_event_id") or ""),
            partial=bool(stub),
            partial_reason=STRAVA_NOTE if stub else "",
            source=raw,
        )

    def intervals(self, activity_id: str) -> list[dict]:
        raw = self.get(f"/activity/{activity_id}/intervals")
        found = []
        for item in (raw or {}).get("icu_intervals") or []:
            distance_km = round((item.get("distance") or 0) / 1000, 3)
            cadence = item.get("average_cadence") or 0
            found.append({
                "type": (item.get("type") or "").lower(),
                "label": item.get("label") or "",
                "start_s": item.get("start_time"),
                "end_s": item.get("end_time"),
                "distance_km": distance_km,
                "moving_time_s": int(item.get("moving_time") or 0),
                "avg_pace": units.pace(item.get("average_speed")),
                "avg_hr": round(item.get("average_heartrate") or 0),
                "max_hr": round(item.get("max_heartrate") or 0),
                "avg_cadence_spm": round(cadence * 2 if 0 < cadence < 120 else cadence),
                "source": item,
            })
        return found

    def streams(self, activity_id: str) -> dict:
        raw = self.get(f"/activity/{activity_id}/streams.json")
        columns: dict[str, list] = {}
        for stream in raw or []:
            kind, data = stream.get("type"), stream.get("data")
            if not kind or data is None:
                continue
            if kind == "latlng":
                columns["lat"], columns["lon"] = data, stream.get("data2") or [None] * len(data)
            else:
                columns.setdefault(STREAMS.get(kind, kind), data)
        return {"activity_id": str(activity_id), "provider": self.name, "columns": columns}

    def wellness(self, oldest: date, newest: date) -> list[dict]:
        rows = self.get(f"{self.athlete_path}/wellness", oldest=oldest.isoformat(), newest=newest.isoformat())
        days = []
        for raw in rows:
            sleep = raw.get("sleepSecs")
            days.append(base.wellness_day(
                date=raw["id"], provider=self.name, fitness=raw.get("ctl"), fatigue=raw.get("atl"), ramp=raw.get("rampRate"),
                resting_hr=raw.get("restingHR"), hrv=raw.get("hrv"), sleep_h=round(sleep / 3600, 2) if sleep else None,
                sleep_score=raw.get("sleepScore"), weight_kg=raw.get("weight"), soreness=raw.get("soreness"),
                tiredness=raw.get("fatigue"), stress=raw.get("stress"), mood=raw.get("mood"), motivation=raw.get("motivation"),
                readiness=raw.get("readiness"), steps=raw.get("steps"), comments=raw.get("comments"), source=raw,
            ))
        return days

    def athlete(self) -> dict:
        raw = self.get(self.athlete_path)
        sports = raw.get("sportSettings") or raw.get("sport_settings") or []
        run = next((s for s in sports if "Run" in (s.get("types") or [s.get("type")])), {})
        return base.profile(
            provider=self.name,
            name=raw.get("name") or "",
            sex=raw.get("sex"),
            birth_date=raw.get("icu_date_of_birth"),
            weight_kg=raw.get("icu_weight") or raw.get("weight"),
            hr_max=run.get("max_hr"),
            hr_rest=raw.get("icu_resting_hr"),
            hr_threshold=run.get("lthr"),
            threshold_pace=units.pace(run.get("threshold_pace")),  # stored as m/s for every sport
            hr_zones=run.get("hr_zones") or [],
            pace_zones=run.get("pace_zones") or [],
            timezone=raw.get("timezone"),
            source={key: value for key, value in raw.items() if not PRIVATE.search(key)},
        )

    def events(self, oldest: date, newest: date) -> list[dict]:
        rows = self.get(f"{self.athlete_path}/events", oldest=oldest.isoformat(), newest=newest.isoformat())
        return [
            {"id": str(raw["id"]), "date": (raw.get("start_date_local") or "")[:10], "name": raw.get("name") or "",
             "external_id": raw.get("external_id") or "", "category": raw.get("category") or "", "type": raw.get("type") or ""}
            for raw in rows
        ]

    def put_event(self, session: dict, context: dict, event_id: str | None = None) -> dict:
        payload, notes = event_payload(session, context)
        response = None
        if event_id:
            try:
                response = self.request("PUT", f"{self.athlete_path}/events/{event_id}", json=payload)
            except ProviderError as error:
                if error.status != 404:  # deleted on intervals.icu: make it again
                    raise
        if response is None:
            response = self.request("POST", f"{self.athlete_path}/events", json=payload)
        raw = response.json()
        structured = bool(((raw.get("workout_doc") or {}).get("steps")))
        if session["modality"] == "run" and session["type"] != "race" and not structured:
            notes.append(f"intervals.icu did not read “{session['title']}” as a structured workout; it will show as text.")
        return {"id": str(raw["id"]), "structured": structured, "notes": notes}

    def delete_event(self, event_id: str) -> None:
        try:
            self.request("DELETE", f"{self.athlete_path}/events/{event_id}")
        except ProviderError as error:
            if error.status != 404:
                raise

    def upload(self, filename: str, content: bytes) -> list[str]:
        raw = self.request("POST", f"{self.athlete_path}/activities", files={"file": (filename, content)}).json()
        found = raw.get("activities") if isinstance(raw, dict) else raw
        ids = [str(item["id"]) for item in found or [] if isinstance(item, dict) and item.get("id")]
        if not ids and isinstance(raw, dict) and raw.get("id"):
            ids = [str(raw["id"])]
        return ids

    def put_wellness(self, day: date, fields: dict) -> None:
        payload = {WELLNESS_WRITE[key]: value for key, value in fields.items() if key in WELLNESS_WRITE and value is not None}
        if fields.get("sleep_h") is not None:
            payload["sleepSecs"] = round(fields["sleep_h"] * 3600)
        unknown = sorted(set(fields) - set(WELLNESS_WRITE) - {"sleep_h"})
        if unknown:
            raise ProviderError(f"intervals.icu wellness has no field for {', '.join(unknown)}. Known: {', '.join(sorted([*WELLNESS_WRITE, 'sleep_h']))}.")
        if payload:
            self.request("PUT", f"{self.athlete_path}/wellness/{day.isoformat()}", json=payload)

    def log_manual(self, entry: dict) -> str:
        kinds = {"run": "Run", "strength": "WeightTraining", "ride": "Ride", "swim": "Swim", "walk": "Walk", "hike": "Hike", "row": "Rowing", "ski": "NordicSki"}
        payload = {
            "start_date_local": f"{entry['date']}T{entry.get('time') or '12:00'}:00",
            "type": kinds.get(entry["sport"], "Workout"),
            "name": entry.get("title") or entry["sport"].capitalize(),
            "moving_time": round(entry["minutes"] * 60),
            "description": entry.get("note") or "",
        }
        if entry.get("distance_km"):
            payload["distance"] = round(entry["distance_km"] * 1000)
        if entry.get("rpe"):
            payload["icu_rpe"] = entry["rpe"]
        raw = self.request("POST", f"{self.athlete_path}/activities/manual/bulk", json=[payload]).json()
        first = raw[0] if isinstance(raw, list) and raw else raw
        return str((first or {}).get("id") or "")

    def gear(self) -> list[dict]:
        return [
            {"id": str(raw.get("id")), "name": raw.get("name") or "", "kind": (raw.get("type") or "").lower(),
             "distance_km": round((raw.get("distance") or 0) / 1000, 1), "retired": bool(raw.get("retired"))}
            for raw in self.get(f"{self.athlete_path}/gear")
        ]
