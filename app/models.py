from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import clock
from app.vdot import parse_time

Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS: tuple[Day, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
Modality = Literal["run", "strength", "cross"]
SCHEMA_VERSION = "3.0"

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
    """API input. The race date window depends on today, so saved plans must not re-check it."""

    @model_validator(mode="after")
    def check_race_date(self):
        today = clock.today()
        if not today + timedelta(weeks=3) <= self.race_date <= today + timedelta(weeks=24):
            raise ValueError("race_date must be between 3 and 24 weeks from today")
        return self


class Step(BaseModel):
    repeat: int  # sets, for strength
    description: str
    distance_km: float
    duration_min: float
    target_pace: str
    reps: int  # 0 when not applicable
    load: str

    # Fields stay required in the LLM schema; this only fills them in for v1 and v2 files.
    @model_validator(mode="before")
    @classmethod
    def fill_old_files(cls, data):
        return {"reps": 0, "load": ""} | data if isinstance(data, dict) else data


class Session(BaseModel):
    day: Day
    type: Literal["easy", "long", "tempo", "intervals", "recovery", "race", "strength", "cross_training"]
    modality: Modality
    title: str
    distance_km: float
    duration_min: float
    target_pace: str
    description: str
    steps: list[Step]

    @model_validator(mode="before")
    @classmethod
    def fill_old_files(cls, data):
        return {"modality": "run"} | data if isinstance(data, dict) else data


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


class RegeneratedPlan(TrainingPlan):
    """A plan plus the schedule after the athlete's feedback, which can change these fields."""

    long_run_day: Day
    available_days: list[Day]
    runs_per_week: int


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
    schema_version: Literal["1.0", "2.0", "3.0"] = SCHEMA_VERSION
    meta: Meta
    request: SavedRequest
    paces: Paces
    plan: DatedPlan


class Lap(BaseModel):
    index: int
    distance_km: float
    time_s: int
    pace: str
    avg_hr: int


class Match(BaseModel):
    planned_date: str = ""  # "" when unplanned
    week_number: int = 0
    session_index: int = -1


class ActivitySummary(BaseModel):
    id: str
    plan_id: str  # activities stay with the plan they were imported under
    source_file: str
    start_time: datetime
    date: date
    distance_km: float
    moving_time_s: int
    elapsed_time_s: int
    avg_pace: str
    avg_hr: int
    max_hr: int
    avg_cadence_spm: int
    elevation_gain_m: float
    laps: list[Lap]
    km_splits: list[Lap]
    user_note: str
    rpe: int
    match: Match
    modality: Modality = "run"  # strength for a watch's gym file, cross for other sports
    sport: str = "running"


class RunReview(BaseModel):
    completion: Literal["as_planned", "partly", "missed_targets", "unplanned"]
    headline: str
    observations: list[str]
    flags: list[str]
    effort: Literal["easier_than_planned", "as_planned", "harder_than_planned"]


class ReviewMeta(BaseModel):
    """Envelope for every saved LLM output other than a plan."""

    model: str
    created_at: str
    usage: Usage


class ReviewFile(BaseModel):
    activity_id: str
    review: RunReview
    meta: ReviewMeta


class Change(BaseModel):
    week_number: int
    day: Day
    summary: str


class AdaptationResult(BaseModel):
    recommendation: Literal["change", "no_change"]
    rationale: str
    changes: list[Change]
    weeks: list[Week]


class Proposal(BaseModel):
    proposal_id: str
    plan_id: str
    based_on_version: int
    scope: Literal["this_week", "next_2_weeks", "rest_of_plan"]
    scope_weeks: list[int]
    user_context: str
    status: Literal["pending", "accepted", "rejected"]
    acceptable: bool
    result: AdaptationResult
    meta: ReviewMeta


class ProgressSummary(BaseModel):
    headline: str
    on_track: Literal["ahead", "on_track", "at_risk", "off_track"]
    observations: list[str]
    suggestions: list[str]


class ProgressFile(BaseModel):
    plan_id: str
    summary: ProgressSummary
    meta: ReviewMeta


class CoachReply(BaseModel):
    reply: str
    action: Literal["none", "propose_change"]
    change_request: str
    scope: Literal["this_week", "next_2_weeks", "rest_of_plan"]
    flags: list[str]


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "coach"]
    created_at: str
    text: str
    action: Literal["none", "proposal_created"] = "none"
    proposal_id: str = ""
    usage: Usage = Usage(input_tokens=0, output_tokens=0)
    model: str = ""
    flags: list[str] = []


class DayLoad(BaseModel):
    date: date
    load: float
    ctl: float
    atl: float
    tsb: float
    km: float


class WeekVolume(BaseModel):
    week_number: int
    start_date: date
    planned_km: float
    actual_km: float
    planned_sessions: int
    completed_sessions: int
    planned_strength: int
    completed_strength: int
    planned_cross_min: float
    actual_cross_min: float
    planned_cross: int = 0
    completed_cross: int = 0


class TrendPoint(BaseModel):
    date: date
    activity_id: str
    type: str
    distance_km: float
    avg_pace_s: int
    avg_hr: int
    efficiency: float


class Snapshot(BaseModel):
    ctl: float
    atl: float
    tsb: float
    ctl_7d_change: float
    weekly_km_4wk_avg: float
    easy_pace_4wk_avg_s: int
    easy_hr_4wk_avg: int


class Metrics(BaseModel):
    as_of: date
    days: list[DayLoad]
    weeks: list[WeekVolume]
    easy_runs: list[TrendPoint]
    quality_runs: list[TrendPoint]
    current: Snapshot


class ManualActivity(BaseModel):
    id: str
    plan_id: str
    date: date
    modality: Modality
    title: str
    duration_min: int
    distance_km: float
    rpe: int
    note: str
    details: list[Step]
    match: Match
