import hashlib
import hmac
import logging
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import date as Date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from app import activities, adapt, chat, clock, config, lifecycle, logbook, logsafe, metrics, progress, review, storage
from app.garmin import sync as garmin_sync
from app.garmin.router import router as garmin_router
from app.generator import APP_VERSION, GenerationError, generate_plan
from app.models import (
    ActivitySummary,
    ChatMessage,
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

DEMO_DATA = Path(__file__).parent.parent / "examples" / "demo" / "data"
DEMO_TODAY = "2026-11-25"
STATIC_DIR = Path(__file__).parent.parent / "static"
SESSION_COOKIE = "openstride_session"
SESSION_SECONDS = 30 * 24 * 3600
WRONG_PASSWORD_DELAY_S = 1
PUBLIC_PATHS = {"/login", "/api/health", "/manifest.webmanifest", "/sw.js"}
ASSET_FOLDERS = ("/icons/", "/fonts/")
ASSET_SUFFIXES = (".css", ".js", ".png", ".svg", ".ico", ".woff", ".woff2")

logger = logging.getLogger("openstride")


@asynccontextmanager
async def lifespan(app: FastAPI):
    problem = config.open_server_problem()
    if problem:
        raise RuntimeError(problem)
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
    garmin_sync.start()
    yield
    garmin_sync.stop()


logsafe.install()
app = FastAPI(title="OpenStride", lifespan=lifespan)


def session_signature(expires: str) -> str:
    """The password is part of it, so changing the password signs every device out."""
    message = f"{expires}.{config.settings.password}".encode()
    return hmac.new(storage.session_secret(), message, hashlib.sha256).hexdigest()


def new_session() -> str:
    expires = str(int(time.time()) + SESSION_SECONDS)
    return f"{expires}.{session_signature(expires)}"


def valid_session(cookie: str | None) -> bool:
    expires, _, signature = (cookie or "").partition(".")
    if not expires.isdecimal() or int(expires) < time.time():
        return False
    return hmac.compare_digest(signature.encode(), session_signature(expires).encode())


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith("/api/"):
        return False
    return path.startswith(ASSET_FOLDERS) or path.endswith(ASSET_SUFFIXES)


def is_https(request: Request) -> bool:
    """Tailscale serve and Caddy end TLS in front of us and say so in this header."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return "https" in (request.url.scheme, forwarded)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.scope["path"]
    if not config.settings.password or is_public(path) or valid_session(request.cookies.get(SESSION_COOKIE)):
        return await call_next(request)
    if path.startswith("/api/") or "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def error_handler(status_code: int, default: str | None = None):
    async def handle(request: Request, error: Exception):
        return JSONResponse({"detail": default or str(error)}, status_code=status_code)

    return handle


app.add_exception_handler(GenerationError, error_handler(502))
app.add_exception_handler(lifecycle.Conflict, error_handler(409))
app.add_exception_handler(storage.NotFound, error_handler(404, "Not found"))
app.add_exception_handler(storage.FileLockTimeout, error_handler(503, "The server is busy. Try again."))


async def plan_changed(request: Request, error: Exception):
    return JSONResponse({"error": "plan_changed"}, status_code=409)


app.add_exception_handler(lifecycle.PlanChanged, plan_changed)


def check_etag(plan_id: str, if_match: str | None) -> None:
    """No header means no check, so older callers keep working."""
    if if_match not in (None, "*") and if_match != storage.plan_etag(plan_id):
        raise lifecycle.PlanChanged(plan_id)


@contextmanager
def checked_plan(plan_id: str, if_match: str | None):
    """Holds the plan lock, so nothing can change the plan between the check and the write.

    For routes with no LLM call; those with one call `check_etag` and lock only the final write.
    """
    with storage.plan_lock(plan_id):
        check_etag(plan_id, if_match)
        yield


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


class ChatInput(BaseModel):
    text: str = Field(min_length=1, pattern=r"\S")


class ProposalRequest(BaseModel):
    scope: Literal["this_week", "next_2_weeks", "rest_of_plan"]
    user_context: str = ""


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


# A plain def, so the delay after a wrong password holds a pool thread, not the event loop.
@app.post("/login", include_in_schema=False)
def login(request: Request, password: str = Form("")) -> RedirectResponse:
    expected = config.settings.password
    if not expected:
        return RedirectResponse("/", status_code=303)
    if not hmac.compare_digest(password.encode(), expected.encode()):
        logger.warning("Failed login from %s", request.client.host if request.client else "unknown")
        time.sleep(WRONG_PASSWORD_DELAY_S)
        return RedirectResponse("/login?failed=1", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        new_session(),
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=is_https(request),
        samesite="lax",
    )
    return response


# Plain defs: FastAPI runs them in a thread pool, so long API calls don't block the server.
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": APP_VERSION, "data_dir_writable": storage.data_dir_writable()}


@app.get("/api/backup")
def download_backup(include_secrets: bool = False) -> FileResponse:
    folder = tempfile.mkdtemp()
    path = storage.write_backup(Path(folder), include_secrets)
    return FileResponse(path, filename=path.name, background=BackgroundTask(shutil.rmtree, folder))


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
def get_plan(plan_id: str, response: Response) -> PlanFile:
    # Before the load: a write in between gives an old tag and a needless 409, never a lost update.
    response.headers["ETag"] = storage.plan_etag(plan_id)
    return lifecycle.require_plan(plan_id)


@app.post("/api/plans/{plan_id}/regenerate")
def regenerate_plan(plan_id: str, body: RegenerateBody, if_match: str | None = Header(None)) -> PlanFile:
    check_etag(plan_id, if_match)
    return lifecycle.regenerate(plan_id, body.feedback, body.request)


@app.post("/api/plans/{plan_id}/accept")
def accept_plan(plan_id: str, if_match: str | None = Header(None)) -> PlanFile:
    with storage.activation_lock(), checked_plan(plan_id, if_match):
        return lifecycle.accept(plan_id)


@app.delete("/api/plans/{plan_id}", status_code=204)
def discard_plan(plan_id: str, if_match: str | None = Header(None)) -> None:
    with checked_plan(plan_id, if_match):
        lifecycle.discard(plan_id)


@app.post("/api/activities")
def upload_activities(files: list[UploadFile], user_note: str = Form(""), rpe: int = Form(0, ge=0, le=10)) -> list[dict]:
    uploads = [(file.filename or "upload", file.file.read()) for file in files]
    return activities.import_files(uploads, user_note, rpe)


@app.get("/api/activities/{activity_id}/series")
def get_series(activity_id: str) -> dict:
    return storage.load_series(activity_id)


@app.get("/api/activities")
def get_activities(plan_id: str | None = None) -> list[dict]:
    return logbook.workouts(plan_id)


@app.get("/api/activities/{activity_id}")
def get_activity(activity_id: str) -> dict:
    return logbook.workout(activity_id)


@app.delete("/api/activities/{activity_id}", status_code=204)
def delete_activity(activity_id: str) -> None:
    activities.delete(activity_id)


@app.patch("/api/activities/{activity_id}")
def edit_activity(activity_id: str, edit: ActivityEdit) -> ActivitySummary:
    match = edit.match and (edit.match.week_number, edit.match.session_index)
    return activities.edit(activity_id, edit.user_note, edit.rpe, match)


@app.post("/api/plans/{plan_id}/manual")
def log_session(plan_id: str, body: ManualInput, if_match: str | None = Header(None)) -> ManualActivity:
    check_etag(plan_id, if_match)
    return logbook.create(plan_id, body.model_dump())


@app.post("/api/manual")
def log_unplanned_session(body: ManualInput) -> ManualActivity:
    return logbook.create(None, body.model_dump())


@app.patch("/api/manual/{manual_id}")
def edit_session(manual_id: str, body: ManualEdit) -> ManualActivity:
    changes = body.model_dump(exclude_none=True, exclude={"match"})
    match = body.match and (body.match.week_number, body.match.session_index)
    return logbook.edit(manual_id, changes, match)


@app.delete("/api/manual/{manual_id}", status_code=204)
def delete_session(manual_id: str) -> None:
    logbook.delete(manual_id)


@app.post("/api/activities/{activity_id}/review")
def rerun_review(activity_id: str) -> ReviewFile:
    return review.review_activity(storage.load_activity(activity_id))


@app.post("/api/plans/{plan_id}/proposals")
def create_proposal(plan_id: str, body: ProposalRequest, if_match: str | None = Header(None)) -> Proposal:
    check_etag(plan_id, if_match)
    return adapt.create_proposal(plan_id, body.scope, body.user_context)


@app.get("/api/plans/{plan_id}/proposals")
def get_proposals(plan_id: str) -> list[Proposal]:
    return storage.list_proposals(plan_id)


@app.post("/api/proposals/{proposal_id}/accept")
def accept_proposal(proposal_id: str, if_match: str | None = Header(None)) -> PlanFile:
    with checked_plan(storage.load_proposal(proposal_id).plan_id, if_match):
        return adapt.accept(proposal_id)


@app.post("/api/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, if_match: str | None = Header(None)) -> Proposal:
    with checked_plan(storage.load_proposal(proposal_id).plan_id, if_match):
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
def restore_version(plan_id: str, version: int, if_match: str | None = Header(None)) -> PlanFile:
    with checked_plan(plan_id, if_match):
        return adapt.restore(plan_id, version)


@app.get("/api/today")
def get_today() -> dict:
    return {"today": clock.today().isoformat()}


@app.get("/api/plans/{plan_id}/status")
def get_status(plan_id: str) -> dict:
    return progress.plan_status(lifecycle.require_plan(plan_id))


@app.get("/api/plans/{plan_id}/progress")
def get_progress(plan_id: str) -> dict:
    plan_file = lifecycle.require_plan(plan_id)
    if plan_file.meta.status == "draft":
        raise lifecycle.Conflict("A draft has no progress yet.")
    return progress.planned_vs_actual(plan_file) | {"latest_summary": progress.latest_summary(plan_id)}


@app.post("/api/plans/{plan_id}/progress/summary")
def create_progress_summary(plan_id: str) -> ProgressFile:
    return progress.summarise(plan_id)


@app.post("/api/plans/{plan_id}/chat")
def send_chat(plan_id: str, body: ChatInput, if_match: str | None = Header(None)) -> dict:
    check_etag(plan_id, if_match)
    return chat.send(plan_id, body.text)


@app.get("/api/plans/{plan_id}/chat")
def get_chat(plan_id: str) -> list[ChatMessage]:
    lifecycle.require_plan(plan_id)
    return storage.load_chat(plan_id)


@app.post("/api/plans/{plan_id}/chat/clear", status_code=204)
def clear_chat(plan_id: str, if_match: str | None = Header(None)) -> None:
    with checked_plan(plan_id, if_match):
        chat.clear(plan_id)


@app.get("/api/plans/{plan_id}/metrics")
def get_metrics(plan_id: str) -> Metrics:
    return metrics.refresh(lifecycle.require_plan(plan_id))


@app.get("/api/plans/{plan_id}/usage")
def get_usage(plan_id: str) -> dict:
    return progress.usage_totals(plan_id)


app.include_router(garmin_router)

# Mounted last so it never shadows API routes; html=True serves index.html at /.
# It also puts /manifest.webmanifest, /sw.js and /icons/* at the root scope the service worker needs.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True))
