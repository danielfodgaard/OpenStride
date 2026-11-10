"""The plan: what the athlete asked for, the weeks, and a proposal to change them.

Input from an agent is read leniently (missing optional fields get defaults); what is stored is complete.
"""
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import clock
from app.vdot import parse_time

Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS: tuple[Day, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
Modality = Literal["run", "strength", "cross"]
SessionType = Literal["easy", "long", "tempo", "intervals", "recovery", "race", "strength", "cross_training"]
MODALITIES: dict[str, Modality] = dict.fromkeys(("easy", "long", "tempo", "intervals", "recovery", "race"), "run") | {
    "strength": "strength",
    "cross_training": "cross",
}
SCHEMA_VERSION = "4.0"

MAX_PACE_MIN_PER_KM = 20  # slower than a walk; VDOT formulas turn meaningless near 40 min/km


class SavedRequest(BaseModel):
    """What the plan was built from, valid forever."""

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
    strength_sessions_per_week: int = Field(default=0, ge=0, le=3)
    cross_training: str = ""
    cross_sessions_per_week: int = Field(default=0, ge=0, le=3)
    equipment: str = ""
    hr_max: int = Field(default=0, ge=0, le=250)  # 0 = unknown
    hr_rest: int = Field(default=0, ge=0, le=150)
    age: int = Field(default=0, ge=0, le=120)

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
        if self.cross_sessions_per_week and not self.cross_training.strip():
            raise ValueError("cross_training must name a sport when cross_sessions_per_week is set")
        if self.hr_max and self.hr_rest and self.hr_max <= self.hr_rest:
            raise ValueError("hr_max must be above hr_rest")
        if self.long_run_day not in self.available_days:
            raise ValueError("long_run_day must be one of available_days")
        if parse_time(self.recent_race_time) / self.recent_race_distance_km > MAX_PACE_MIN_PER_KM:
            raise ValueError(f"recent race is slower than {MAX_PACE_MIN_PER_KM} min/km; check distance and time")
        if self.goal_time and parse_time(self.goal_time) / self.race_distance_km > MAX_PACE_MIN_PER_KM:
            raise ValueError(f"goal is slower than {MAX_PACE_MIN_PER_KM} min/km; check distance and time")
        return self


class PlanRequest(SavedRequest):
    """A request for a new plan. The race date window depends on today, so saved plans must not re-check it."""

    @model_validator(mode="after")
    def check_race_date(self):
        today = clock.today()
        if not today + timedelta(weeks=3) <= self.race_date <= today + timedelta(weeks=24):
            raise ValueError("race_date must be between 3 and 24 weeks from today")
        return self


class Step(BaseModel):
    repeat: int = 1  # sets, for strength
    description: str = ""
    distance_km: float = 0
    duration_min: float = 0
    target_pace: str = ""
    reps: int = 0  # 0 when not applicable
    load: str = ""


class Session(BaseModel):
    id: str = ""  # stable across versions of the plan; assigned in code when missing
    day: Day
    type: SessionType
    modality: Modality
    title: str
    distance_km: float = 0
    duration_min: float = 0
    target_pace: str = ""
    description: str = ""
    steps: list[Step] = []
    status: Literal["planned", "skipped", "replaced"] = "planned"
    moved_from: date | None = None  # the original date of a moved session
    edit_note: str = ""

    @model_validator(mode="before")
    @classmethod
    def fill_modality(cls, data):
        if isinstance(data, dict) and "modality" not in data and data.get("type") in MODALITIES:
            return data | {"modality": MODALITIES[data["type"]]}
        return data


class Week(BaseModel):
    week_number: int
    phase: Literal["base", "build", "peak", "taper"]
    focus: str = ""
    planned_km: float = 0
    sessions: list[Session]


class DatedSession(Session):
    date: date


class DatedWeek(Week):
    start_date: date
    sessions: list[DatedSession]


class DatedPlan(BaseModel):
    schema_version: str = SCHEMA_VERSION
    summary: str = ""
    warnings: list[str] = []
    weeks: list[DatedWeek]

    def week(self, number: int) -> DatedWeek | None:
        return next((w for w in self.weeks if w.week_number == number), None)

    def week_of(self, day: date) -> DatedWeek | None:
        return next((w for w in self.weeks if w.start_date <= day <= w.start_date + timedelta(days=6)), None)

    def sessions(self) -> list[DatedSession]:
        return [s for w in self.weeks for s in w.sessions]

    def session(self, session_id: str) -> DatedSession | None:
        return next((s for s in self.sessions() if s.id == session_id), None)


class Paces(BaseModel):
    current_vdot: float
    goal_vdot: float | None
    easy: str
    marathon: str
    threshold: str
    interval: str
    repetition: str
    race_pace: str


class PlanDoc(BaseModel):
    """The three files under plan/, together."""

    request: SavedRequest
    paces: Paces
    plan: DatedPlan


class Change(BaseModel):
    week: int
    day: Day
    summary: str


class Draft(BaseModel):
    """What an agent hands to `openstride plan draft`: the weeks it wants, and why."""

    rationale: str = ""
    changes: list[Change] = []
    request: dict | None = None  # a new plan's request, or changed constraints for the current one
    summary: str | None = None
    warnings: list[str] | None = None
    weeks: list[Week]


class Proposal(BaseModel):
    id: str
    created_at: str
    kind: Literal["change", "new_plan"]
    status: Literal["pending", "approved", "rejected", "stale"] = "pending"
    base_commit: str  # the plan's commit this was drafted against; "" when there was no plan
    rationale: str
    changes: list[Change] = []
    warnings: list[str] = []  # trade-offs shown above the diff; they never block
    author: str = "agent"  # agent or analyst
    decided_at: str = ""
    decision_note: str = ""
    doc: PlanDoc  # the whole plan as it would be
