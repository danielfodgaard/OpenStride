import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import storage
from app.generator import GenerationError, generate_plan
from app.models import PlanFile, SavedRequest
from app.prompt import week_starts

SAMPLE = json.loads(Path("examples/sample_plan.json").read_text())
REQUEST = SavedRequest.model_validate(SAMPLE["request"])
TODAY = date(2026, 10, 31)  # the sample's creation date, which makes it a 10-week plan


class FakeClient:
    """Replays canned messages through the client.messages.stream() surface."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []
        self.messages = self

    def stream(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        final = SimpleNamespace(get_final_message=lambda: self.replies.pop(0))
        return Stream(final)


class Stream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self.final

    def __exit__(self, *exc):
        return False


def reply(plan, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json.dumps(plan))],
        usage=SimpleNamespace(input_tokens=100, output_tokens=200),
    )


def broken_plan():
    plan = json.loads(json.dumps(SAMPLE["plan"]))
    plan["weeks"][0]["sessions"][0]["target_pace"] = "9:99/km"
    return plan


@pytest.fixture(autouse=True)
def plans_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PLANS_DIR", tmp_path)
    return tmp_path


def test_week_starts_on_monday_on_or_after_today():
    assert week_starts(date(2026, 11, 2), date(2026, 11, 29))[0] == date(2026, 11, 2)
    assert week_starts(date(2026, 11, 3), date(2026, 11, 29))[0] == date(2026, 11, 9)


def test_week_count_includes_the_race_week():
    assert len(week_starts(TODAY, date(2027, 1, 10))) == 10
    assert len(week_starts(TODAY, date(2027, 1, 11))) == 11


def test_valid_plan_is_dated_and_saved(plans_dir):
    client = FakeClient(reply(SAMPLE["plan"]))
    result = generate_plan(REQUEST, client, TODAY)

    assert len(client.prompts) == 1
    assert result.plan.weeks[0].start_date == date(2026, 11, 2)
    assert result.plan.weeks[0].sessions[0].date == date(2026, 11, 3)
    assert result.plan.weeks[-1].sessions[-1].date == REQUEST.race_date
    assert result.meta.usage.output_tokens == 200
    assert result.meta.id.endswith("_hm")
    saved = PlanFile.model_validate_json((plans_dir / f"{result.meta.id}.json").read_text())
    assert saved == result


def test_retry_sends_errors_and_counts_usage_of_both_calls():
    client = FakeClient(reply(broken_plan()), reply(SAMPLE["plan"]))
    result = generate_plan(REQUEST, client, TODAY)

    assert "Fix these problems:" in client.prompts[1] and "9:99/km" in client.prompts[1]
    assert result.meta.usage.input_tokens == 200
    assert result.plan.warnings == SAMPLE["plan"]["warnings"]


def test_remaining_problems_become_warnings():
    client = FakeClient(reply(broken_plan()), reply(broken_plan()))
    result = generate_plan(REQUEST, client, TODAY)
    assert any("9:99/km" in warning for warning in result.plan.warnings)


def test_wrong_week_count_still_produces_a_file():
    plan = json.loads(json.dumps(SAMPLE["plan"]))
    plan["weeks"].append(plan["weeks"][-1])
    result = generate_plan(REQUEST, FakeClient(reply(plan), reply(plan)), TODAY)
    assert len(result.plan.weeks) == 11


@pytest.mark.parametrize("stop_reason,message", [("max_tokens", "cut off"), ("refusal", "declined")])
def test_stop_reasons_are_clear_errors(stop_reason, message):
    with pytest.raises(GenerationError, match=message):
        generate_plan(REQUEST, FakeClient(reply(SAMPLE["plan"], stop_reason)), TODAY)


def test_unparseable_output_is_a_clear_error():
    with pytest.raises(GenerationError, match="unexpected format"):
        generate_plan(REQUEST, FakeClient(reply({"summary": "no weeks"})), TODAY)
