"""The record in the workspace: sync, the calendar, and noticing edits made on the provider."""
import json
from datetime import date

import pytest

from app import matching, mirror, plan, providers, workspace
from app.providers.demo import Demo
from tests.conftest import approve, week_draft


class Editable(Demo):
    """The demo provider with a record a test can change, as an athlete changes theirs on the provider."""

    def __init__(self, store):
        super().__init__(store=store)
        self.rows = [row | {"intervals": []} for row in super().activities(date(2026, 1, 1), date(2027, 1, 1))]
        self.fail_intervals = False

    def activities(self, oldest, newest):
        return [r for r in self.rows if oldest.isoformat() <= r["date"] <= newest.isoformat()]

    def intervals(self, activity_id):
        if self.fail_intervals:
            raise providers.ProviderError("intervals are down")
        return [{"type": "lap", "source": {"x": None, "y": 1}}]


@pytest.fixture
def provider(ws, tmp_path):
    made = Editable(tmp_path / "provider.json")
    providers.use(made)
    return made


def test_sync_writes_the_record_and_commits_it(provider):
    result = mirror.sync()
    assert len(result["new"]) == 15 and result["wellness_days"] == 38
    rows = mirror.rows()
    assert [r["start_local"] for r in rows] == sorted(r["start_local"] for r in rows)
    record = mirror.load(rows[0]["id"])
    assert record["key"] == rows[0]["key"] and record["provider"] == "demo"
    assert mirror.load(rows[0]["key"]) == record
    assert len(mirror.wellness()) == 38 and mirror.profile()["hr_max"] == 188 and mirror.profile()["gear"][0]["name"] == "Daily trainer"
    assert workspace.changed(["activities", "wellness", "athlete/profile.yaml"]) == []
    assert workspace.log(limit=1)[0]["message"] == "Sync from demo: 15 new"


def test_a_second_sync_brings_only_what_changed(provider):
    mirror.sync()
    assert mirror.sync()["new"] == [] and mirror.sync()["updated"] == []
    provider.rows[-1] = provider.rows[-1] | {"name": "Renamed", "source": provider.rows[-1]["source"] | {"edited": True}}
    result = mirror.sync()
    assert result["new"] == [] and len(result["updated"]) == 1
    assert mirror.load(result["updated"][0])["name"] == "Renamed"


def test_an_activity_removed_on_the_provider_disappears(provider):
    mirror.sync()
    gone = provider.rows.pop()
    result = mirror.sync()
    assert len(result["removed"]) == 1 and all(r["id"] != gone["id"] for r in mirror.rows())
    assert not (mirror.folder() / f"{result['removed'][0]}.json").exists()


def test_fields_without_a_value_are_left_out_of_source(provider):
    mirror.sync()
    record = mirror.load(mirror.rows()[0]["key"])
    assert record["intervals"] == [{"type": "lap", "source": {"y": 1}}]


def test_a_failed_detail_call_does_not_lose_the_activity(provider):
    provider.fail_intervals = True
    result = mirror.sync()
    assert len(result["new"]) == 15 and any("intervals are down" in note for note in result["notes"])


def test_streams_are_fetched_once_and_kept_out_of_git(provider):
    mirror.sync()
    key = next(r["key"] for r in mirror.rows() if r["sport"] == "run")
    data = mirror.streams(key)
    assert len(data["columns"]["time_s"]) > 100
    assert (mirror.folder() / f"{key}.streams.json").exists()
    assert workspace.changed(["activities"]) == []
    with pytest.raises(mirror.NotFound):
        mirror.streams("nope")


def test_matching_prefers_corrections_then_the_providers_pairing_then_the_day(demo_ws):
    doc = plan.load()
    rows = mirror.rows()
    automatic = matching.compute(doc.plan, rows)
    run = next(r for r in rows if r["sport"] == "run")
    planned = automatic.by_activity[run["key"]]
    assert planned and doc.plan.session(planned).date.isoformat() == run["date"]
    other = next(s for s in doc.plan.sessions() if s.id != planned and s.modality == "run")
    paired = matching.compute(doc.plan, [r | {"planned_event_id": "E1"} if r["key"] == run["key"] else r for r in rows], pairs={"E1": other.id})
    assert paired.by_activity[run["key"]] == other.id
    note = workspace.root() / "notes" / run["key"]
    note.mkdir(parents=True)
    (note / "match.json").write_text(json.dumps({"session_id": ""}))
    corrected = matching.compute(doc.plan, rows, matching.overrides(), {"E1": other.id})
    assert corrected.by_activity[run["key"]] == "" and planned not in corrected.done()


def test_the_calendar_gets_what_is_ahead_and_only_what_changed(demo_ws):
    doc = plan.load()
    first = mirror.push_calendar(doc)
    ahead = [s for s in doc.plan.sessions() if s.date.isoformat() >= "2026-11-25"]
    assert len(first["sent"]) == len(ahead) and first["removed"] == []
    assert mirror.push_calendar(doc) == {"sent": [], "removed": [], "notes": []}
    draft = week_draft(6)
    easy = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "easy")
    easy["distance_km"] += 2
    gone = next(s for s in draft["weeks"][0]["sessions"] if s["type"] == "strength")
    gone["status"], gone["edit_note"] = "skipped", "No gym"
    draft["changes"] = [{"week": 6, "day": easy["day"], "summary": "longer"}, {"week": 6, "day": gone["day"], "summary": "skipped"}]
    second = mirror.push_calendar(approve(plan.draft(draft)))
    assert second["sent"] == [easy["id"]] and second["removed"] == [gone["id"]]
    assert gone["id"] not in mirror.calendar()
    assert workspace.changed(["plan"]) == []
    assert plan.plan_commit() != workspace.last_commit("plan/calendar.json")  # sending it doesn't count as changing the plan


def test_an_edit_on_the_providers_calendar_is_reported_once(demo_ws):
    doc = plan.load()
    mirror.push_calendar(doc)
    assert mirror.drift() == []
    provider = providers.active()
    data = provider.saved()
    session_id, entry = next((sid, e) for sid, e in mirror.calendar().items() if e["date"] > "2026-11-27")
    data["events"][entry["event_id"]]["date"] = "2026-12-30"
    other = next(e for sid, e in mirror.calendar().items() if sid != session_id and e["date"] > "2026-11-27")
    del data["events"][other["event_id"]]
    provider.save(data)
    found = mirror.drift()
    assert len(found) == 2 and any("was moved" in line for line in found) and any("was removed" in line for line in found)
    assert mirror.drift() == []
