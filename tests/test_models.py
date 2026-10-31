import json
from datetime import date, timedelta
from pathlib import Path

import anthropic
import pytest
from pydantic import ValidationError

from app.models import PlanFile, PlanRequest, TrainingPlan


def request_data(**changes):
    data = {
        "goal_description": "Sub-1:30 half marathon",
        "race_distance_km": 21.0975,
        "goal_time": "1:30:00",
        "race_date": date.today() + timedelta(weeks=10),
        "recent_race_distance_km": 10,
        "recent_race_time": "44:00",
        "current_weekly_km": 40,
        "longest_recent_run_km": 16,
        "runs_per_week": 4,
        "available_days": ["Tue", "Thu", "Sat", "Sun"],
        "long_run_day": "Sun",
        "experience": "intermediate",
        "notes": "",
    }
    return data | changes


def test_valid_request():
    assert PlanRequest(**request_data()).runs_per_week == 4


def test_goal_time_is_optional():
    assert PlanRequest(**request_data(goal_time=None)).goal_time is None


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"race_date": date.today() + timedelta(weeks=2)}, "between 3 and 24 weeks"),
        ({"race_date": date.today() + timedelta(weeks=25)}, "between 3 and 24 weeks"),
        ({"runs_per_week": 1}, "greater than or equal to 2"),
        ({"runs_per_week": 8}, "less than or equal to 7"),
        ({"runs_per_week": 5}, "at least runs_per_week"),
        ({"available_days": ["Tue", "Tue", "Tue", "Sun"]}, "at least runs_per_week"),
        ({"long_run_day": "Mon"}, "long_run_day"),
        ({"goal_time": "soon"}, "h:mm:ss"),
        ({"recent_race_time": "12:99"}, "below 60"),
        ({"recent_race_time": "9:00:00"}, "recent race is slower"),
        ({"goal_time": "12:00:00"}, "goal is slower"),
        ({"race_distance_km": 0}, "greater than 0"),
        ({"current_weekly_km": float("inf")}, "finite"),
        ({"available_days": ["Tue", "Thu", "Sat", "Sunday"]}, "Input should be"),
    ],
)
def test_invalid_requests_have_readable_messages(changes, message):
    with pytest.raises(ValidationError, match=message):
        PlanRequest(**request_data(**changes))


def test_llm_schema_is_compatible():
    def walk(node):
        if isinstance(node, dict):
            assert "anyOf" not in node and "oneOf" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(anthropic.transform_schema(TrainingPlan))


def test_sample_plan_parses_even_after_its_race_date_passes():
    PlanFile.model_validate_json(Path("examples/sample_plan.json").read_text())


def test_sample_request_matches_the_sample_plan():
    request = json.loads(Path("examples/sample_request.json").read_text())
    assert json.loads(Path("examples/sample_plan.json").read_text())["request"] == request
