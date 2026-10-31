import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import date as Date
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import activities, adapt, lifecycle, logbook, metrics, progress, review, storage
from app.generator import GenerationError, generate_plan
from app.models import (
    ActivitySummary,
    ManualActivity,
    Metrics,
    Modality,
    PlanFile,
    PlanRequest,
    ProgressFile,
    Proposal,
    ReviewFile,
    Step,
)

load_dotenv()

DEMO_DATA = Path(__file__).parent.parent / "examples" / "demo" / "data"
DEMO_TODAY = "2026-11-11"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("OPENSTRIDE_DEMO"):
        # Work on a throwaway copy, with the clock pinned to where the demo story stands.
        with tempfile.TemporaryDirectory() as folder:
            storage.DATA_DIR = Path(folder) / "data"
            shutil.copytree(DEMO_DATA, storage.DATA_DIR)
            os.environ["OPENSTRIDE_TODAY"] = DEMO_TODAY
            yield
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    yield


app = FastAPI(title="OpenStride", lifespan=lifespan)


def error_handler(status_code: int, default: str | None = None):
    async def handle(request: Request, error: Exception):
        return JSONResponse({"detail": default or str(error)}, status_code=status_code)

    return handle


app.add_exception_handler(GenerationError, error_handler(502))
app.add_exception_handler(lifecycle.Conflict, error_handler(409))
app.add_exception_handler(storage.NotFound, error_handler(404, "Not found"))


class RegenerateBody(BaseModel):
    feedback: str
    request: PlanRequest | None = None


class MatchEdit(BaseModel):
    week_number: int  # 0 with session_index -1 means unplanned
    session_index: int


class ActivityEdit(BaseModel):
    user_note: str | None = None
    rpe: int | None = Field(default=None, ge=0, le=10)
    match: MatchEdit | None = None


class ManualInput(BaseModel):
    date: Date
    modality: Modality
    title: str = ""
    duration_min: int = Field(ge=0, le=1440)
    distance_km: float = Field(default=0, ge=0, allow_inf_nan=False)
    rpe: int = Field(default=0, ge=0, le=10)
    note: str = ""
    details: list[Step] = []


class ManualEdit(BaseModel):
    date: Date | None = None
    modality: Modality | None = None
    title: str | None = None
    duration_min: int | None = Field(default=None, ge=0, le=1440)
    distance_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rpe: int | None = Field(default=None, ge=0, le=10)
    note: str | None = None
    details: list[Step] | None = None
    match: MatchEdit | None = None


class ProposalRequest(BaseModel):
    scope: Literal["this_week", "next_2_weeks", "rest_of_plan"]
    user_context: str = ""


# Plain defs: FastAPI runs them in a thread pool, so long API calls don't block the server.
@app.post("/api/plan")
def create_plan(request: PlanRequest) -> PlanFile:
    return generate_plan(request)


@app.get("/api/plans")
def get_plans() -> list[dict]:
    return storage.list_plans()


@app.get("/api/plans/active")
def get_active_plan() -> PlanFile:
    plan_file = lifecycle.active_plan()
    if plan_file is None:
        raise storage.NotFound("active")
    return plan_file


@app.post("/api/plans/import")
def import_plan(file: UploadFile) -> PlanFile:
    try:
        return lifecycle.import_plan(file.file.read())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str) -> PlanFile:
    return lifecycle.require_plan(plan_id)


@app.post("/api/plans/{plan_id}/regenerate")
def regenerate_plan(plan_id: str, body: RegenerateBody) -> PlanFile:
    return lifecycle.regenerate(plan_id, body.feedback, body.request)


@app.post("/api/plans/{plan_id}/accept")
def accept_plan(plan_id: str) -> PlanFile:
    return lifecycle.accept(plan_id)


@app.delete("/api/plans/{plan_id}", status_code=204)
def discard_plan(plan_id: str) -> None:
    lifecycle.discard(plan_id)


@app.post("/api/activities")
def upload_activities(files: list[UploadFile], user_note: str = Form(""), rpe: int = Form(0, ge=0, le=10)) -> list[dict]:
    uploads = [(file.filename or "upload", file.file.read()) for file in files]
    return activities.import_files(uploads, user_note, rpe)


@app.get("/api/activities")
def get_activities(plan_id: str) -> list[dict]:
    return logbook.all_items(plan_id)


@app.patch("/api/activities/{activity_id}")
def edit_activity(activity_id: str, edit: ActivityEdit) -> ActivitySummary:
    match = edit.match and (edit.match.week_number, edit.match.session_index)
    return activities.edit(activity_id, edit.user_note, edit.rpe, match)


@app.post("/api/plans/{plan_id}/manual")
def log_session(plan_id: str, body: ManualInput) -> ManualActivity:
    return logbook.create(plan_id, body.model_dump())


@app.patch("/api/manual/{manual_id}")
def edit_session(manual_id: str, body: ManualEdit) -> ManualActivity:
    changes = body.model_dump(exclude_unset=True, exclude={"match"})
    match = body.match and (body.match.week_number, body.match.session_index)
    return logbook.edit(manual_id, changes, match)


@app.delete("/api/manual/{manual_id}", status_code=204)
def delete_session(manual_id: str) -> None:
    logbook.delete(manual_id)


@app.post("/api/activities/{activity_id}/review")
def rerun_review(activity_id: str) -> ReviewFile:
    return review.review_activity(storage.load_activity(activity_id))


@app.post("/api/plans/{plan_id}/proposals")
def create_proposal(plan_id: str, body: ProposalRequest) -> Proposal:
    return adapt.create_proposal(plan_id, body.scope, body.user_context)


@app.get("/api/plans/{plan_id}/proposals")
def get_proposals(plan_id: str) -> list[Proposal]:
    return storage.list_proposals(plan_id)


@app.post("/api/proposals/{proposal_id}/accept")
def accept_proposal(proposal_id: str) -> PlanFile:
    return adapt.accept(proposal_id)


@app.post("/api/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str) -> Proposal:
    return adapt.reject(proposal_id)


@app.get("/api/plans/{plan_id}/versions")
def get_versions(plan_id: str) -> list[dict]:
    lifecycle.require_plan(plan_id)
    return [
        {"version": p.meta.version, "history": [h.model_dump() for h in p.meta.history if h.version == p.meta.version]}
        for p in storage.list_versions(plan_id)
    ]


@app.get("/api/plans/{plan_id}/versions/{version}")
def get_version(plan_id: str, version: int) -> PlanFile:
    return storage.load_version(plan_id, version)


@app.post("/api/plans/{plan_id}/restore/{version}")
def restore_version(plan_id: str, version: int) -> PlanFile:
    return adapt.restore(plan_id, version)


@app.get("/api/plans/{plan_id}/progress")
def get_progress(plan_id: str) -> dict:
    table = progress.planned_vs_actual(lifecycle.require_active(plan_id))
    return table | {"latest_summary": progress.latest_summary(plan_id)}


@app.post("/api/plans/{plan_id}/progress/summary")
def create_progress_summary(plan_id: str) -> ProgressFile:
    return progress.summarise(plan_id)


@app.get("/api/plans/{plan_id}/metrics")
def get_metrics(plan_id: str) -> Metrics:
    return metrics.refresh(lifecycle.require_plan(plan_id))


@app.get("/api/plans/{plan_id}/usage")
def get_usage(plan_id: str) -> dict:
    return progress.usage_totals(plan_id)


# Mounted last so it never shadows API routes; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True))
