"""When the agent acts without being asked, and when it holds back."""
import asyncio
from datetime import date, datetime

import pytest

from app import hub as hub_module, mirror, plan, providers, triggers, workspace
from app.agent import Agent
from app.hub import hub
from app.providers.demo import Demo
from app.runtime.scripted import Scripted

WEDNESDAY_NOON = datetime(2026, 11, 25, 12, 0)


@pytest.fixture
def queued(demo_ws):
    """An agent that is never started, so what a trigger enqueues can be read off its queue."""
    async def make():
        hub.bind(asyncio.get_running_loop())
        hub.agent = Agent(hub, Scripted(lambda request: []))

    asyncio.run(make())
    return lambda: [(item.origin, item.text) for item in hub.agent.pending]


def settings(**triggers_on):
    current = workspace.settings()
    current["triggers"] |= triggers_on
    current["sync_minutes"] = 0
    (workspace.root() / "settings.yaml").write_text(workspace.dump_yaml(current))


class Growing(Demo):
    def __init__(self, store):
        super().__init__(store=store)
        self.extra = []

    def activities(self, oldest, newest):
        return super().activities(oldest, newest) + [r for r in self.extra if oldest.isoformat() <= r["date"] <= newest.isoformat()]


def test_the_first_sync_is_history_not_news(ws, tmp_path):
    async def make():
        hub.bind(asyncio.get_running_loop())
        hub.agent = Agent(hub, Scripted(lambda request: []))

    asyncio.run(make())
    providers.use(Demo(store=tmp_path / "p.json"))
    result = triggers.sync_now(hub)
    assert len(result["new"]) == 15 and result["briefed"] == [] and hub.agent.pending == []


def test_a_new_activity_briefs_the_agent_once(queued, tmp_path):
    provider = Growing(tmp_path / "p.json")
    providers.use(provider)
    triggers.sync_now(hub)
    assert queued() == []
    base = provider.activities(date(2026, 11, 22), date(2026, 11, 22))[0]
    provider.extra = [base | {"id": "new1", "date": "2026-11-25", "start_local": "2026-11-25T07:10:00", "name": "Morning run", "distance_km": 9.0, "intervals": []}]
    result = triggers.sync_now(hub)
    assert len(result["briefed"]) == 1
    origin, text = queued()[0]
    assert origin == "new_activity" and f"activities/{result['briefed'][0]}.json" in text and "9 km" in text and "review-run" in text
    triggers.sync_now(hub)
    assert len(queued()) == 1


def test_an_old_activity_arriving_late_is_not_news_and_the_trigger_can_be_off(queued, tmp_path):
    provider = Growing(tmp_path / "p.json")
    providers.use(provider)
    triggers.sync_now(hub)
    base = provider.activities(date(2026, 11, 22), date(2026, 11, 22))[0]
    provider.extra = [base | {"id": "old1", "date": "2026-11-12", "start_local": "2026-11-12T07:10:00", "intervals": []}]
    assert triggers.sync_now(hub)["briefed"] == [] and queued() == []
    settings(new_activity=False)
    provider.extra.append(base | {"id": "new2", "date": "2026-11-25", "start_local": "2026-11-25T08:00:00", "intervals": []})
    triggers.sync_now(hub)
    assert queued() == []


def test_a_failed_sync_is_told_once(queued):
    class Down(Demo):
        def activities(self, oldest, newest):
            raise providers.ProviderError("intervals.icu could not be reached: ConnectError.")

    providers.use(Down())
    assert "error" in triggers.sync_now(hub) and "error" in triggers.sync_now(hub)
    notices = [e for e in hub.stream.history() if e["type"] == "notice"]
    assert len(notices) == 1 and "Sync failed" in notices[0]["text"]


def test_the_week_end_review_fires_once_after_the_week_is_over(queued):
    settings()
    assert triggers.tick(hub, WEDNESDAY_NOON) == ["week_end"]  # week 3 ended on Sunday
    assert queued() == [("week_end", "Week 3 of the plan is over. Follow the weekly-review skill.")]
    assert triggers.tick(hub, WEDNESDAY_NOON) == []
    assert triggers.tick(hub, datetime(2026, 11, 30, 9, 0)) == ["week_end"]
    assert queued()[-1][1].startswith("Week 4")


def test_a_long_gap_does_not_bring_back_old_weeks(queued):
    settings()
    assert triggers.tick(hub, datetime(2026, 11, 28, 12, 0)) == []  # week 3 ended six days ago


def test_quiet_hours_hold_everything_until_morning(queued):
    settings(morning_brief=True)
    assert triggers.tick(hub, datetime(2026, 11, 25, 23, 0)) == []
    assert triggers.tick(hub, datetime(2026, 11, 25, 6, 30)) == []
    assert set(triggers.tick(hub, datetime(2026, 11, 25, 7, 45))) == {"week_end", "morning_brief"}
    assert triggers.tick(hub, datetime(2026, 11, 25, 8, 0)) == []


def test_unquoted_times_in_settings_still_mean_times(queued):
    (workspace.root() / "settings.yaml").write_text("sync_minutes: 0\nquiet_hours:\n  from: 22:15\n  to: 06:45\ntriggers:\n  morning_brief: true\n  morning_brief_time: 6:50\n  week_end: false\n")
    loaded = workspace.settings()
    assert loaded["quiet_hours"] == {"from": "22:15", "to": "06:45"} and loaded["triggers"]["morning_brief_time"] == "06:50"
    assert triggers.tick(hub, datetime(2026, 11, 25, 6, 40)) == []
    assert triggers.tick(hub, datetime(2026, 11, 25, 6, 55)) == ["morning_brief"]


def test_an_unanswered_check_in_is_not_followed_by_another(queued):
    settings(morning_brief=True, week_end=False)
    hub.stream.append("message", role="coach", text="Week three went well.", origin="week_end")
    assert triggers.tick(hub, datetime(2026, 11, 25, 7, 45)) == []
    hub.stream.append("message", role="athlete", text="Thanks")
    assert triggers.tick(hub, datetime(2026, 11, 25, 7, 50)) == ["morning_brief"]


def test_the_race_report_fires_the_day_after_the_race(queued):
    settings(week_end=False)
    assert triggers.tick(hub, datetime(2027, 1, 10, 12, 0)) == []
    assert triggers.tick(hub, datetime(2027, 1, 11, 12, 0)) == ["plan_end"]
    assert triggers.tick(hub, datetime(2027, 1, 12, 12, 0)) == []


def test_a_wellness_signal_needs_a_baseline_and_a_real_departure(queued):
    settings(wellness_signal=True, week_end=False)
    assert triggers.wellness_signal(date(2026, 11, 25)) is None
    path = workspace.root() / "wellness" / "2026-11.jsonl"
    path.write_text(path.read_text().replace('"date": "2026-11-25"', '"date": "2026-11-25", "x": 1').replace('"resting_hr": 5', '"resting_hr": 5', 1))
    import json

    days = [json.loads(line) for line in path.read_text().splitlines()]
    days[-1] |= {"resting_hr": 64, "sleep_h": 4.5}
    path.write_text("".join(json.dumps(day) + "\n" for day in days))
    signal = triggers.wellness_signal(date(2026, 11, 25))
    assert "Resting heart rate is 64" in signal and "sleep was 4.5 h" in signal
    assert triggers.tick(hub, WEDNESDAY_NOON) == ["wellness_signal"]
    assert triggers.tick(hub, WEDNESDAY_NOON) == []


def test_the_monthly_cap_pauses_check_ins_and_says_so_once(queued):
    settings()
    current = workspace.settings()
    current["budget"]["monthly_usd"] = 5
    (workspace.root() / "settings.yaml").write_text(workspace.dump_yaml(current))
    hub_module.record_usage(role="coach", turn="t", origin="athlete", model="m", cost_usd=5.2, usage={"input_tokens": 10})
    assert hub_module.over_budget()
    assert triggers.tick(hub, WEDNESDAY_NOON) == ["over_budget"] and triggers.tick(hub, WEDNESDAY_NOON) == ["over_budget"]
    assert queued() == []
    notices = [e for e in hub.stream.history() if e["type"] == "notice"]
    assert len(notices) == 1 and "$5 cap" in notices[0]["text"]
    assert hub_module.usage()["cost_usd"] == 5.2 and hub_module.usage("2026-10")["turns"] == 0


def test_a_calendar_edit_on_the_provider_briefs_the_agent(queued):
    settings()
    mirror.push_calendar(plan.load())
    provider = providers.active()
    data = provider.saved()
    event_id = next(e["event_id"] for e in mirror.calendar().values() if e["date"] > "2026-11-27")
    del data["events"][event_id]
    provider.save(data)
    triggers.sync_now(hub)
    origin, text = queued()[-1]
    assert origin == "calendar_drift" and "was removed" in text
