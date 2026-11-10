"""The contract, and both providers against it. intervals.icu is driven through a fake HTTP transport
with responses in the shape its API returns."""
import json
from datetime import date

import httpx
import pytest

from app.providers import base
from app.providers.demo import Demo
from app.providers.intervals_icu import IntervalsIcu, event_payload, workout_text

OLDEST, NEWEST = date(2026, 11, 1), date(2026, 11, 25)

ACTIVITY = {
    "id": "i555", "start_date_local": "2026-11-20T18:02:11", "type": "Run", "name": "Evening tempo", "distance": 10020.0, "moving_time": 3000,
    "elapsed_time": 3100, "total_elevation_gain": 85.2, "average_speed": 3.34, "average_heartrate": 151.6, "max_heartrate": 171,
    "average_cadence": 86.0, "icu_training_load": 62, "icu_intensity": 78.4, "icu_rpe": None, "perceived_exertion": 6.0, "paired_event_id": 901,
    "source": "GARMIN_CONNECT", "icu_hr_zone_times": [100, 900, 1500, 500, 0], "calories": None,
}
STRAVA_STUB = {"id": "i556", "start_date_local": "2026-11-21T07:00:00", "type": "Run", "name": "Morning", "source": "STRAVA", "_note": "STRAVA activities are not available via the API"}
RIDE = {"id": "i557", "start_date_local": "2026-11-22T09:00:00", "type": "VirtualRide", "name": "Zwift", "distance": 25000, "moving_time": 3600, "average_speed": 6.9, "average_cadence": 88}
WELLNESS = [{"id": "2026-11-20", "ctl": 41.2, "atl": 48.0, "rampRate": 1.4, "restingHR": 49, "hrv": 61.5, "sleepSecs": 27000, "weight": 71.5, "soreness": 2, "fatigue": 3, "comments": "calf tight", "locked": None}]
ATHLETE = {
    "id": "i123", "name": "Dana Runner", "email": "dana@example.com", "sex": "F", "icu_date_of_birth": "1990-05-01", "icu_weight": 60.5, "icu_resting_hr": 48,
    "timezone": "Europe/Copenhagen", "icu_api_key": "secret",
    "sportSettings": [
        {"id": 1, "types": ["Ride", "VirtualRide"], "max_hr": 180, "lthr": 165, "threshold_pace": None},
        {"id": 2, "types": ["Run", "VirtualRun", "TrailRun"], "max_hr": 188, "lthr": 172, "threshold_pace": 3.6101, "hr_zones": [140, 155, 165, 172, 180, 188], "pace_zones": [77.5, 87.7, 94.3, 100, 103.4, 111.5]},
    ],
}
INTERVALS = {"id": "i555", "icu_intervals": [
    {"type": "WORK", "start_time": 600, "end_time": 1500, "distance": 3500.0, "moving_time": 900, "average_speed": 3.88, "average_heartrate": 166.3, "max_heartrate": 171, "average_cadence": 89},
    {"type": "RECOVERY", "start_time": 1500, "end_time": 1680, "distance": 400.0, "moving_time": 180, "average_speed": 2.2, "average_heartrate": 150},
]}
STREAMS = [
    {"type": "time", "data": [0, 1, 2]}, {"type": "heartrate", "data": [100, 110, 120]}, {"type": "velocity_smooth", "data": [3.0, 3.1, 3.2]},
    {"type": "distance", "data": [0, 3.0, 6.1]}, {"type": "latlng", "data": [55.1, 55.2, 55.3], "data2": [12.1, 12.2, 12.3]}, {"type": "ground_contact", "data": [240, 241, 242]},
]


class FakeIcu:
    """Just enough of the API to exercise every call the provider makes, and a log of what it was sent."""

    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []
        self.events = {901: {"id": 901, "start_date_local": "2026-11-27T00:00:00", "name": "Tempo 5 km", "external_id": "openstride-s9", "category": "WORKOUT", "type": "Run"}}
        self.status: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        body = None
        if request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        self.calls.append((request.method, path, body))
        assert request.headers["authorization"].startswith("Basic ")
        if path in self.status:
            return httpx.Response(self.status.pop(path), text="nope", headers={"retry-after": "0"})
        if request.method == "GET":
            if path == "/athlete/i123":
                return httpx.Response(200, json=ATHLETE)
            if path == "/athlete/i123/activities":
                assert request.url.params["oldest"] == "2026-11-01" and request.url.params["newest"] == "2026-11-25T23:59:59"
                return httpx.Response(200, json=[ACTIVITY, STRAVA_STUB, RIDE])
            if path == "/activity/i555/intervals":
                return httpx.Response(200, json=INTERVALS)
            if path == "/activity/i555/streams.json":
                return httpx.Response(200, json=STREAMS)
            if path == "/athlete/i123/wellness":
                return httpx.Response(200, json=WELLNESS)
            if path == "/athlete/i123/events":
                return httpx.Response(200, json=list(self.events.values()))
            if path == "/athlete/i123/gear":
                return httpx.Response(200, json=[{"id": "g1", "name": "Pegasus", "type": "Shoes", "distance": 412300.0, "retired": None}])
        if request.method == "POST" and path == "/athlete/i123/events":
            new_id = max(self.events) + 1
            self.events[new_id] = body | {"id": new_id}
            return httpx.Response(200, json=body | {"id": new_id, "workout_doc": {"steps": [{}] if "Pace" in body.get("description", "") or "km" in body.get("description", "") else []}})
        if request.method == "PUT" and path.startswith("/athlete/i123/events/"):
            event_id = int(path.rsplit("/", 1)[1])
            if event_id not in self.events:
                return httpx.Response(404, text="not found")
            self.events[event_id] = body | {"id": event_id}
            return httpx.Response(200, json=body | {"id": event_id, "workout_doc": {"steps": [{}]}})
        if request.method == "DELETE" and path.startswith("/athlete/i123/events/"):
            return httpx.Response(200 if self.events.pop(int(path.rsplit("/", 1)[1]), None) else 404, json={})
        if request.method == "POST" and path == "/athlete/i123/activities":
            return httpx.Response(200, json={"icu_athlete_id": "i123", "activities": [{"id": "i999"}]})
        if request.method == "PUT" and path.startswith("/athlete/i123/wellness/"):
            return httpx.Response(200, json=body | {"id": path.rsplit("/", 1)[1]})
        if request.method == "POST" and path == "/athlete/i123/activities/manual/bulk":
            return httpx.Response(200, json=[body[0] | {"id": "i1000"}])
        return httpx.Response(500, text=f"unexpected {request.method} {path}")


@pytest.fixture
def fake():
    return FakeIcu()


@pytest.fixture
def icu(fake):
    return IntervalsIcu("i123", "key", transport=httpx.MockTransport(fake))


def test_both_providers_meet_the_contract(icu, tmp_path):
    assert base.conformance(Demo(store=tmp_path / "demo.json"), OLDEST, NEWEST) == []
    assert base.conformance(icu, OLDEST, NEWEST) == []


def test_conformance_catches_a_provider_that_breaks_the_shape(tmp_path):
    class Broken(Demo):
        def activities(self, oldest, newest):
            rows = super().activities(oldest, newest)
            rows[0] = {k: v for k, v in rows[0].items() if k != "sport"}
            rows[1] = rows[1] | {"sport": "jogging"}
            return rows

    problems = base.conformance(Broken(store=tmp_path / "demo.json"), OLDEST, NEWEST)
    assert any("lacks ['sport']" in p for p in problems) and any("'jogging'" in p for p in problems)


def test_an_activity_in_the_neutral_shape(icu):
    run, stub, ride = icu.activities(OLDEST, NEWEST)
    assert run["id"] == "i555" and run["provider"] == "intervals.icu" and run["sport"] == "run"
    assert run["date"] == "2026-11-20" and run["start_local"] == "2026-11-20T18:02:11"
    assert run["distance_km"] == 10.02 and run["avg_pace"] == "4:59/km" and run["avg_hr"] == 152
    assert run["avg_cadence_spm"] == 172  # one leg's strides, doubled
    assert run["load"] == 62 and run["intensity"] == 78.4 and run["rpe"] == 6.0  # the Strava-style RPE when the native one is empty
    assert run["planned_event_id"] == "901" and run["partial"] is False
    assert run["source"]["icu_hr_zone_times"] == [100, 900, 1500, 500, 0]
    assert ride["sport"] == "ride" and ride["avg_pace"] == "" and ride["avg_cadence_spm"] == 88


def test_a_strava_stub_is_marked_partial_with_the_reason(icu):
    stub = icu.activities(OLDEST, NEWEST)[1]
    assert stub["partial"] is True and "Strava" in stub["partial_reason"] and stub["distance_km"] == 0


def test_intervals_streams_wellness_and_athlete(icu):
    work, recovery = icu.intervals("i555")
    assert work["type"] == "work" and work["distance_km"] == 3.5 and work["avg_pace"] == "4:18/km" and work["avg_cadence_spm"] == 178
    assert recovery["type"] == "recovery"
    columns = icu.streams("i555")["columns"]
    assert columns["time_s"] == [0, 1, 2] and columns["hr"] == [100, 110, 120] and columns["speed_mps"][2] == 3.2
    assert columns["lat"] == [55.1, 55.2, 55.3] and columns["lon"] == [12.1, 12.2, 12.3] and columns["ground_contact"] == [240, 241, 242]
    day = icu.wellness(OLDEST, NEWEST)[0]
    assert day["date"] == "2026-11-20" and day["fitness"] == 41.2 and day["fatigue"] == 48.0 and day["form"] == -6.8
    assert day["resting_hr"] == 49 and day["sleep_h"] == 7.5 and day["tiredness"] == 3 and day["comments"] == "calf tight"
    athlete = icu.athlete()
    assert athlete["hr_max"] == 188 and athlete["hr_rest"] == 48 and athlete["hr_threshold"] == 172 and athlete["threshold_pace"] == "4:37/km"
    assert "email" not in athlete["source"] and "icu_api_key" not in athlete["source"]
    assert icu.gear() == [{"id": "g1", "name": "Pegasus", "kind": "shoes", "distance_km": 412.3, "retired": False}]


SESSION = {
    "id": "s9", "date": "2026-11-27", "day": "Fri", "type": "intervals", "modality": "run", "title": "4 × 0.4 km", "distance_km": 6.8, "duration_min": 0,
    "target_pace": "3:55/km", "description": "Run the reps evenly.",
    "steps": [
        {"repeat": 1, "description": "Warm up", "distance_km": 2, "duration_min": 0, "target_pace": "5:11-6:13/km", "reps": 0, "load": ""},
        {"repeat": 4, "description": "Repetition", "distance_km": 0.4, "duration_min": 0, "target_pace": "3:55/km", "reps": 0, "load": ""},
        {"repeat": 3, "description": "Jog recovery", "distance_km": 0, "duration_min": 1.5, "target_pace": "", "reps": 0, "load": ""},
        {"repeat": 1, "description": "Cool down", "distance_km": 2, "duration_min": 0, "target_pace": "easy", "reps": 0, "load": ""},
    ],
}


def test_a_run_becomes_workout_text_the_watch_can_take():
    text, notes = workout_text(SESSION)
    assert text.splitlines() == [
        "- Warm up 2km 6:13-5:11/km Pace",
        "",
        "3x",
        "- 400mtr 3:55/km Pace",
        "- Recovery 1m30s",
        "",
        "- 400mtr 3:55/km Pace",  # four reps and three jogs: three rounds, then the last rep
        "- Cool down 2km",
    ]
    assert notes == ["No pace target on the watch for “Cool down”: could not read “easy”."]
    steady, _ = workout_text({"distance_km": 8, "duration_min": 0, "target_pace": "5:11-6:13/km", "steps": []})
    assert steady == "- 8km 6:13-5:11/km Pace"
    timed, _ = workout_text({"distance_km": 0, "duration_min": 45, "target_pace": "", "steps": []})
    assert timed == "- 45m"


def test_event_payloads_by_kind():
    payload, _ = event_payload(SESSION, {})
    assert payload["category"] == "WORKOUT" and payload["type"] == "Run" and payload["start_date_local"] == "2026-11-27T00:00:00"
    assert payload["external_id"] == "openstride-s9" and "moving_time" not in payload and payload["description"].endswith("Run the reps evenly.")
    strength = {"id": "s2", "date": "2026-11-27", "type": "strength", "modality": "strength", "title": "Strength A", "duration_min": 30, "distance_km": 0,
                "description": "After the run.", "steps": [{"repeat": 3, "description": "Split squat", "reps": 8, "load": "2 x 10 kg", "duration_min": 0, "target_pace": ""}]}
    payload, _ = event_payload(strength, {})
    assert payload["type"] == "WeightTraining" and payload["moving_time"] == 1800
    assert "• Split squat 3 x 8 (2 x 10 kg)" in payload["description"] and "\n- " not in payload["description"]
    ride = {"id": "s3", "date": "2026-11-27", "type": "cross_training", "modality": "cross", "title": "Easy spin", "duration_min": 45, "distance_km": 0, "description": "", "steps": [], "target_pace": "RPE 4"}
    assert event_payload(ride, {"cross_training": "cycling"})[0]["type"] == "Ride"
    assert event_payload(ride | {"title": "Pool"}, {"cross_training": "swimming"})[0]["type"] == "Swim"
    race = SESSION | {"type": "race", "title": "Half marathon", "steps": []}
    assert event_payload(race, {})[0]["category"] == "RACE_A"


def test_events_are_created_replaced_recreated_and_deleted(icu, fake):
    made = icu.put_event(SESSION, {})
    assert made["structured"] is True and made["id"] == "902"
    assert icu.put_event(SESSION | {"title": "5 × 0.4 km"}, {}, made["id"])["id"] == "902"
    assert fake.events[902]["name"] == "5 × 0.4 km"
    del fake.events[902]  # the athlete deleted it on intervals.icu
    assert icu.put_event(SESSION, {}, "902")["id"] == "902"  # made again, not an error
    icu.delete_event("902")
    icu.delete_event("902")  # already gone is fine
    assert [e["id"] for e in icu.events(OLDEST, NEWEST)] == ["901"]


def test_an_unparsed_workout_is_reported(icu):
    odd = SESSION | {"steps": [], "distance_km": 0, "duration_min": 0, "target_pace": "by feel"}
    made = icu.put_event(odd, {})
    assert made["structured"] is False and any("did not read" in note for note in made["notes"])


def test_upload_wellness_and_manual_log(icu, fake):
    assert icu.upload("run.fit", b"FITDATA") == ["i999"]
    icu.put_wellness(date(2026, 11, 25), {"soreness": 3, "tiredness": 2, "sleep_h": 6.5, "comments": "left calf", "weight_kg": 71})
    assert fake.calls[-1] == ("PUT", "/athlete/i123/wellness/2026-11-25", {"soreness": 3, "fatigue": 2, "comments": "left calf", "weight": 71, "sleepSecs": 23400})
    with pytest.raises(base.ProviderError, match="no field for vibes"):
        icu.put_wellness(date(2026, 11, 25), {"vibes": 10})
    assert icu.log_manual({"date": "2026-11-24", "sport": "strength", "minutes": 30, "rpe": 6, "title": "Gym"}) == "i1000"
    assert fake.calls[-1][2] == [{"start_date_local": "2026-11-24T12:00:00", "type": "WeightTraining", "name": "Gym", "moving_time": 1800, "description": "", "icu_rpe": 6}]


def test_errors_are_messages_for_the_athlete(icu, fake):
    fake.status["/athlete/i123"] = 401
    with pytest.raises(base.ProviderError, match="refused the API key"):
        icu.check()
    fake.status["/athlete/i123"] = 429  # one polite retry, then it works
    assert icu.check() == {"athlete": "Dana Runner", "id": "i123"}
    with pytest.raises(base.ProviderError, match="No intervals.icu API key"):
        IntervalsIcu("i123", "").check()

    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(base.ProviderError, match="could not be reached"):
        IntervalsIcu("i123", "key", transport=httpx.MockTransport(down)).check()


def test_the_demo_provider_keeps_its_own_calendar_and_notes(tmp_path):
    demo = Demo(store=tmp_path / "demo.json")
    made = demo.put_event(SESSION, {})
    assert demo.events(OLDEST, date(2026, 12, 31))[0]["name"] == "4 × 0.4 km"
    demo.delete_event(made["id"])
    assert demo.events(OLDEST, date(2026, 12, 31)) == []
    demo.put_wellness(date(2026, 11, 20), {"soreness": 3})
    assert next(d for d in demo.wellness(OLDEST, NEWEST) if d["date"] == "2026-11-20")["soreness"] == 3
    ident = demo.log_manual({"date": "2026-11-24", "sport": "strength", "minutes": 30})
    assert any(a["id"] == ident for a in demo.activities(OLDEST, NEWEST))
    with pytest.raises(base.Unsupported):
        demo.upload("run.fit", b"x")
