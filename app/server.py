"""The web server: one stream to read, one box to write in, and the approval gate.

It holds what must be right whatever the model does: the gate, the write boundary, the queue, the cards,
the provider connection and the triggers. The page it serves computes nothing.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import shutil
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app import agent_api, analyst, cards, clock, config, hub as hub_module, logsafe, mirror, ops, plan, providers, spool, triggers, workspace
from app.agent import Agent, make_runtime
from app.hub import hub
from app.providers import ProviderError
from app.runtime import claude_sdk

APP_VERSION = "0.2.0"
STATIC_DIR = Path(__file__).parent.parent / "static"
SESSION_COOKIE = "openstride_session"
SESSION_SECONDS = 30 * 24 * 3600
WRONG_PASSWORD_DELAY_S = 1
PUBLIC_PATHS = {"/login", "/api/health", "/manifest.webmanifest", "/sw.js"}
ASSET_FOLDERS = ("/icons/", "/fonts/")
ASSET_SUFFIXES = (".css", ".js", ".png", ".svg", ".ico", ".woff", ".woff2")
MAX_UPLOAD_BYTES = 25_000_000
MAX_TEXT_BYTES = 400_000
KEEPALIVE_S = 20
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    problem = config.open_server_problem()
    if problem:
        raise RuntimeError(problem)
    demo_folder = None
    if os.environ.get("OPENSTRIDE_DEMO") and not os.environ.get("OPENSTRIDE_DATA_DIR"):
        # Work on a throwaway copy, with the clock pinned to where the demo story stands.
        from app import demo

        demo_folder = tempfile.mkdtemp(prefix="openstride-demo-")
        demo.prepare(Path(demo_folder))
    await asyncio.to_thread(workspace.init)
    hub.bind(asyncio.get_running_loop())
    hub.runtime = make_runtime()
    hub.agent = Agent(hub, hub.runtime)
    hub.agent.start()
    if os.environ.get("OPENSTRIDE_DEMO"):
        from app import demo

        demo.seed(hub)
    background = []
    if not os.environ.get("OPENSTRIDE_NO_AGENT_SPOOL"):
        background.append(asyncio.create_task(spool.watch(agent_api.create())))
    if not os.environ.get("OPENSTRIDE_NO_TRIGGERS"):
        background.append(asyncio.create_task(triggers.run(hub)))
    logger.info("OpenStride %s: provider %s, runtime %s, sandbox %s", APP_VERSION, config.settings.provider, hub.runtime.name, "on" if claude_sdk.sandbox_on() else "off")
    if hub.runtime.name == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY is not set: the page will load, but the agent can't answer until it is. Copy .env.example to .env and add your key.")
    if hub.runtime.name == "claude" and not claude_sdk.sandbox_on():
        logger.warning("The OS sandbox is off (`openstride doctor` says why). The plan and the record are still guarded by the tool-call check and the check after each turn.")
    try:
        yield
    finally:
        await hub.agent.stop()
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if demo_folder:
            shutil.rmtree(demo_folder, ignore_errors=True)


logsafe.install()
app = FastAPI(title="OpenStride", lifespan=lifespan)


# --- login -------------------------------------------------------------------------------------
def session_secret() -> bytes:
    path = workspace.state() / "secrets" / "session_secret"
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace.write_file(path, secrets.token_bytes(32), mode=0o600)
    return path.read_bytes()


def session_signature(expires: str) -> str:
    """The password is part of it, so changing the password signs every device out."""
    message = f"{expires}.{config.settings.password}".encode()
    return hmac.new(session_secret(), message, hashlib.sha256).hexdigest()


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


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


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
    response.set_cookie(SESSION_COOKIE, new_session(), max_age=SESSION_SECONDS, httponly=True, secure=is_https(request), samesite="lax")
    return response


# --- errors ------------------------------------------------------------------------------------
@app.exception_handler(plan.PlanError)
async def plan_error(request: Request, error: plan.PlanError):
    return JSONResponse({"detail": str(error), "problems": error.problems}, status_code=409)


@app.exception_handler(cards.CardError)
async def card_error(request: Request, error: cards.CardError):
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(ProviderError)
async def provider_error(request: Request, error: ProviderError):
    return JSONResponse({"detail": str(error)}, status_code=502)


# --- the stream --------------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/status")
def status() -> dict:
    provider = providers.active()
    synced = mirror.last_synced()
    return {
        "version": APP_VERSION,
        "provider": {"name": provider.name if provider else None, "connected": provider is not None,
                     "capabilities": provider.capabilities.as_dict() if provider else {},
                     "last_sync_at": synced.isoformat() if synced else None, "error": triggers.state().get("sync_error") or ""},
        "runtime": hub.runtime.name if hub.runtime else None,
        "sandbox": "on" if claude_sdk.sandbox_on() else "off",
        "busy": hub.agent.busy() if hub.agent else False,
        "usage": hub_module.usage() | {"monthly_cap_usd": workspace.settings()["budget"]["monthly_usd"]},
        "today": clock.today().isoformat(),
        "demo": bool(os.environ.get("OPENSTRIDE_DEMO")),
    }


@app.get("/api/stream")
def stream_history(after: int = 0, before: int | None = None, limit: int = 80) -> dict:
    events = hub.stream.history(after=after, before=before, limit=min(limit, 500))
    return {"events": events, "busy": hub.agent.busy() if hub.agent else False}


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    queue = hub.stream.subscribe()

    async def feed():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                yield sse(event)
        finally:
            hub.stream.unsubscribe(queue)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # a proxy must not hold the events back
    return StreamingResponse(feed(), media_type="text/event-stream", headers=headers)


def take_files(files: list[tuple[str, bytes]]) -> list[str]:
    """Recordings go to the provider and come back through sync; anything else is kept for the agent to read."""
    lines, recordings = [], []
    provider = providers.active()
    for name, content in files:
        if name.lower().endswith(ops.UPLOAD_SUFFIXES):
            if provider is None or not provider.capabilities.upload:
                lines.append(f"(The athlete attached the recording {name}, but {provider.name if provider else 'no provider'} can't take files. Tell them.)")
                continue
            try:
                provider.upload(name, content)
                recordings.append(name)
            except ProviderError as error:
                lines.append(f"(The athlete attached the recording {name}; sending it to {provider.name} failed: {error})")
        else:
            lines.append(f"(The athlete attached a file, saved at {ops.save_upload(name, content)}.)")
    if recordings:
        result = triggers.sync_now(hub, announce=False)
        keys = result.get("new", []) + result.get("updated", [])
        triggers.handle_sync(hub, {"new": keys}, workspace.settings(), announce=False)
        where = ", ".join(f"activities/{key}.json" for key in keys) or "nothing new appeared yet; the provider may still be processing it"
        lines.append(f"(The athlete attached {', '.join(recordings)}, sent to {provider.name} and synced: {where}.)")
    return lines


@app.post("/api/messages")
async def send_message(text: str = Form(""), files: list[UploadFile] | None = None) -> dict:
    uploads = []
    for file in files or []:
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{file.filename} is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB.")
        uploads.append((file.filename or "upload", content))
    text = text.strip()
    if not text and not uploads:
        raise HTTPException(422, "Say something, or attach a file.")
    shown = text or f"Attached {', '.join(name for name, _ in uploads)}"
    event = hub.stream.append("message", role="athlete", text=shown, attachments=[name for name, _ in uploads])
    notes = await run_in_threadpool(take_files, uploads) if uploads else []
    hub.agent.enqueue("message", "\n".join([text or "I attached this.", *notes]), "athlete")
    return {"event": event}


@app.post("/api/turn/stop")
async def stop_turn() -> dict:
    return {"stopped": hub.agent.interrupt()}


@app.get("/api/glance")
def glance() -> dict:
    return cards.glance()


@app.get("/api/card")
def card(type: str, args: str = "{}") -> dict:
    try:
        parsed = json.loads(args)
    except ValueError as error:
        raise HTTPException(422, "args must be JSON") from error
    if type in cards.STATIC:
        raise HTTPException(422, "This card's data is kept with its event.")
    return cards.build(type, parsed)


# --- the gate ----------------------------------------------------------------------------------
class RejectBody(BaseModel):
    note: str = ""


def approve_and_send(proposal_id: str) -> dict:
    proposal = plan.load_proposal(proposal_id)
    doc = plan.apply(proposal_id, plan.approval_token(proposal))
    sent = {"sent": [], "removed": [], "notes": []}
    if providers.active() is not None:
        try:
            sent = mirror.push_calendar(doc)
        except ProviderError as error:
            sent["notes"].append(f"The calendar was not updated: {error}")
    return {"proposal": proposal, "calendar": sent}


@app.post("/api/proposals/{proposal_id}/approve")
async def approve(proposal_id: str) -> dict:
    """The athlete's Approve. The only caller of plan.apply in the server, and not reachable from an agent's shell."""
    try:
        result = await run_in_threadpool(approve_and_send, proposal_id)
    except plan.PlanError as error:
        hub.stream.publish("plan_changed")
        if "out of date" in str(error) or "has changed" in str(error):
            hub.agent.add_notice(f"The athlete tried to approve proposal {proposal_id}, but it was out of date: {error}")
        raise
    calendar, proposal = result["calendar"], result["proposal"]
    provider = providers.active()
    where = f" {len(calendar['sent'])} session(s) sent to {provider.name}." if provider and provider.capabilities.calendar else ""
    hub.agent.add_notice(
        f"The athlete approved proposal {proposal_id} ({proposal.rationale.splitlines()[0][:100]}). plan/ is updated.{where}"
        + (" Notes: " + " ".join(calendar["notes"]) if calendar["notes"] else "")
    )
    hub.stream.append("notice", level="ok", text="Plan updated." + where + (" " + " ".join(calendar["notes"]) if calendar["notes"] else ""), proposal_id=proposal_id)
    hub.stream.publish("plan_changed")
    return {"status": "approved", "calendar": calendar}


@app.post("/api/proposals/{proposal_id}/reject")
async def reject(proposal_id: str, body: RejectBody) -> dict:
    proposal = await run_in_threadpool(plan.reject, proposal_id, body.note)
    hub.stream.publish("plan_changed")
    if body.note.strip():
        hub.stream.append("message", role="athlete", text=f"Rejected: {body.note.strip()}")
        hub.agent.enqueue("message", f"(I rejected proposal {proposal_id}.) {body.note.strip()}", "athlete")
    else:
        hub.agent.add_notice(f"The athlete rejected proposal {proposal_id} without a note.")
    return {"status": proposal.status}


# --- the data drawer: read-only, for trust -----------------------------------------------------
HIDDEN = (".git", "scratch", ".claude")


@app.get("/api/files")
def files() -> dict:
    root = workspace.root()
    found = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or workspace.under(rel, HIDDEN) or path.name.endswith(".tmp"):
            continue
        found.append({"path": rel, "size": path.stat().st_size, "tier": workspace.tier(rel)})
    return {"files": found}


def workspace_file(path: str) -> Path:
    rel = workspace.relative(path)
    if rel is None or workspace.under(rel, (".git",)) or not (workspace.root() / rel).is_file():
        raise HTTPException(404, "Not found")
    return workspace.root() / rel


@app.get("/api/file")
def file(path: str) -> dict:
    target = workspace_file(path)
    rel = target.relative_to(workspace.root().resolve()).as_posix() if target.is_absolute() else path
    if target.suffix.lower() in IMAGE_TYPES:
        return {"path": rel, "image": True, "tier": workspace.tier(rel)}
    data = target.read_bytes()
    try:
        text = data[:MAX_TEXT_BYTES].decode()
    except UnicodeDecodeError:
        text = "(binary file)"
    return {"path": rel, "text": text, "truncated": len(data) > MAX_TEXT_BYTES, "tier": workspace.tier(rel), "log": workspace.log(rel, limit=20)}


@app.get("/api/raw")
def raw(path: str) -> FileResponse:
    target = workspace_file(path)
    if target.suffix.lower() not in IMAGE_TYPES:
        raise HTTPException(415, "Only images are served raw.")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")


@app.get("/api/log")
def log(path: str = "", limit: int = 50) -> dict:
    return {"log": workspace.log(*([path] if path else []), limit=min(limit, 200))}


@app.post("/api/sync")
async def sync() -> dict:
    result = await run_in_threadpool(triggers.sync_now, hub)
    if result.get("error"):
        raise HTTPException(502, result["error"])
    return {key: result.get(key) for key in ("new", "updated", "removed", "wellness_days", "notes")}


@app.get("/api/reports")
def reports() -> dict:
    return {"reports": analyst.reports()}


def write_backup(folder: Path) -> Path:
    """A zip of the data folder without secrets, sockets and locks, which unzips into a working one."""
    target = folder / f"openstride-backup-{date.today()}.zip"
    folder.mkdir(parents=True, exist_ok=True)
    data_dir = config.settings.data_dir
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(data_dir.rglob("*")):
            relative = path.relative_to(data_dir)
            if not path.is_file() or path.is_symlink() or path.name.endswith((".lock", ".tmp", ".zip", ".sock")):
                continue
            if "secrets" in relative.parts or relative.parts[:2] == ("state", "analyst"):
                continue
            try:
                archive.write(path, relative)
            except FileNotFoundError:
                pass
    return target


@app.get("/api/backup")
def backup() -> FileResponse:
    folder = tempfile.mkdtemp()
    path = write_backup(Path(folder))
    return FileResponse(path, filename=path.name, background=BackgroundTask(shutil.rmtree, folder))


# Mounted last so it never shadows API routes; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True))
