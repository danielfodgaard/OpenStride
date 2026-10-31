from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.vdot import parse_time

Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS: tuple[Day, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

MAX_PACE_MIN_PER_KM = 20  # slower than a walk; VDOT formulas turn meaningless near 40 min/km


class SavedRequest(BaseModel):
    """Request fields as stored in a plan file, valid forever."""

    model_config = ConfigDict(allow_inf_nan=False)

    goal_description: str = Field(min_length=1)
    race_distance_km: float = Field(gt=0)
    goal_time: str | None = None
    race_date: date
    recent_race_distance_km: float = Field(gt=0)
    recent_race_time: str
    current_weekly_km: float = Field(ge=0)
    longest_recent_run_km: float = Field(ge=0)
    runs_per_week: int = Field(ge=2, le=7)
    available_days: list[Day]
    long_run_day: Day
    experience: Literal["beginner", "intermediate", "advanced"]
    notes: str = ""

    @field_validator("goal_time", "recent_race_time")
    @classmethod
    def check_time(cls, value: str | None) -> str | None:
        if value is not None:
            parse_time(value)
        return value

    @model_validator(mode="after")
    def check_consistency(self):
        if len(set(self.available_days)) < self.runs_per_week:
            raise ValueError("available_days must contain at least runs_per_week different days")
        if self.long_run_day not in self.available_days:
            raise ValueError("long_run_day must be one of available_days")
        if parse_time(self.recent_race_time) / self.recent_race_distance_km > MAX_PACE_MIN_PER_KM:
            raise ValueError(f"recent race is slower than {MAX_PACE_MIN_PER_KM} min/km; check distance and time")
        if self.goal_time and parse_time(self.goal_time) / self.race_distance_km > MAX_PACE_MIN_PER_KM:
            raise ValueError(f"goal is slower than {MAX_PACE_MIN_PER_KM} min/km; check distance and time")
        return self


class PlanRequest(SavedRequest):
    """API input. The race date window depends on today, so saved plans must not re-check it."""

    @model_validator(mode="after")
    def check_race_date(self):
        today = date.today()
        if not today + timedelta(weeks=3) <= self.race_date <= today + timedelta(weeks=24):
            raise ValueError("race_date must be between 3 and 24 weeks from today")
        return self


class Step(BaseModel):
    repeat: int
    description: str
    distance_km: float
    duration_min: float
    target_pace: str


class Session(BaseModel):
    day: Day
    type: Literal["easy", "long", "tempo", "intervals", "recovery", "race"]
    title: str
    distance_km: float
    duration_min: float
    target_pace: str
    description: str
    steps: list[Step]


class Week(BaseModel):
    week_number: int
    phase: Literal["base", "build", "peak", "taper"]
    focus: str
    planned_km: float
    sessions: list[Session]


class TrainingPlan(BaseModel):
    summary: str
    warnings: list[str]
    weeks: list[Week]


class DatedSession(Session):
    date: date


class DatedWeek(Week):
    start_date: date
    sessions: list[DatedSession]


class DatedPlan(TrainingPlan):
    weeks: list[DatedWeek]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class HistoryEntry(BaseModel):
    version: int
    proposal_id: str
    accepted_at: str
    rationale: str


class Meta(BaseModel):
    id: str
    created_at: str
    app_version: str
    model: str
    usage: Usage
    # Defaults let v1.0 files load unchanged.
    version: int = 1
    history: list[HistoryEntry] = []
    status: Literal["draft", "active", "archived"] = "draft"
    accepted_at: str = ""
    feedback_history: list[str] = []


class Paces(BaseModel):
    current_vdot: float
    goal_vdot: float | None
    easy: str
    marathon: str
    threshold: str
    interval: str
    repetition: str
    race_pace: str


class PlanFile(BaseModel):
    schema_version: Literal["1.0", "2.0"] = "2.0"
    meta: Meta
    request: SavedRequest
    paces: Paces
    plan: DatedPlan
