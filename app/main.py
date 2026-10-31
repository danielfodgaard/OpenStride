import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import activities, lifecycle, review, storage
from app.generator import GenerationError, generate_plan
from app.models import ActivitySummary, PlanFile, PlanRequest, ReviewFile

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    return [activities.with_review(activity) for activity in storage.list_activities(plan_id)]


@app.patch("/api/activities/{activity_id}")
def edit_activity(activity_id: str, edit: ActivityEdit) -> ActivitySummary:
    match = edit.match and (edit.match.week_number, edit.match.session_index)
    return activities.edit(activity_id, edit.user_note, edit.rpe, match)


@app.post("/api/activities/{activity_id}/review")
def rerun_review(activity_id: str) -> ReviewFile:
    return review.review_activity(storage.load_activity(activity_id))


# Mounted last so it never shadows API routes; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True))
