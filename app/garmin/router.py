"""Garmin endpoints. With a capability off these say so, except what needs no Garmin.

A GarminError that reaches a route becomes a 502 through the handler in main.py.
"""
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import lifecycle, workout_fit
from app.garmin import delivery, login, sync
from app.garmin.provider import disconnect, make_pusher, push_mode, push_refusal, status, sync_mode

router = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


class CodeBody(BaseModel):
    code: str


def not_configured(error: str = "garmin_not_configured") -> JSONResponse:
    return JSONResponse({"error": error}, status_code=501)


def pusher():
    """The provider, or the response to send instead."""
    if push_mode() == "off":
        return None, not_configured("push_not_supported" if sync_mode() != "off" else "garmin_not_configured")
    blocked = push_refusal()
    if blocked:
        return None, JSONResponse({"error": "push_refused", "detail": blocked}, status_code=403)
    return make_pusher(), None


def login_address_allowed(request: Request) -> bool:
    """A Garmin password only ever goes to this machine or to the Tailscale HTTPS address."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    https = "https" in (request.url.scheme, request.headers.get("x-forwarded-proto", "").split(",")[0].strip())
    name = urlsplit(f"//{host}").hostname or ""
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != host:  # another site's page posting here
        return False
    return name in ("127.0.0.1", "localhost") or (name.endswith(".ts.net") and https)


def login_refused(request: Request) -> JSONResponse | None:
    _, answer = pusher()
    if answer:
        return answer
    if not login_address_allowed(request):
        detail = "Log in from this computer at 127.0.0.1, or over the Tailscale HTTPS address."
        return JSONResponse({"error": "login_address_refused", "detail": detail}, status_code=403)
    return None


@router.get("/api/garmin/status")
def garmin_status(request: Request):
    return status() | {"login_allowed": login_address_allowed(request)}


@router.post("/api/garmin/login")
def garmin_login(body: LoginBody, request: Request):
    return login_refused(request) or {"status": login.start(body.email, body.password)}


@router.post("/api/garmin/login/mfa")
def garmin_login_code(body: CodeBody, request: Request):
    return login_refused(request) or {"status": login.finish(body.code)}


@router.post("/api/garmin/disconnect")
def garmin_disconnect():
    login.clear()
    disconnect()
    return {"status": "disconnected"}


@router.post("/api/garmin/sync")
def garmin_sync():
    return sync.sync() if sync_mode() != "off" else not_configured()


@router.get("/api/plans/{plan_id}/sessions/{week}/{index}/workout.fit")
def workout_fit_file(plan_id: str, week: int, index: int):
    session = delivery.session_at(lifecycle.require_plan(plan_id), week, index)
    if session.modality != "run":
        return JSONResponse({"error": "not_a_run"}, status_code=422)
    built = workout_fit.build(session)
    headers = {"Content-Disposition": f'attachment; filename="{workout_fit.file_name(session)}"'}
    if built.notes:  # header values are latin-1
        headers["X-OpenStride-Notes"] = " ".join(built.notes).encode("ascii", "replace").decode()
    return Response(built.data, media_type="application/vnd.ant.fit", headers=headers)


@router.post("/api/plans/{plan_id}/sessions/{week}/{index}/send-to-garmin")
def send_session(plan_id: str, week: int, index: int):
    provider, answer = pusher()
    if answer:
        return answer
    plan_file = lifecycle.require_active(plan_id)
    if delivery.session_at(plan_file, week, index).modality != "run":
        return JSONResponse({"error": "not_a_run"}, status_code=422)
    return delivery.send_session(provider, plan_file, week, index)


@router.get("/api/plans/{plan_id}/weeks/{number}/garmin")
def week_status(plan_id: str, number: int):
    return delivery.week_summary(lifecycle.require_plan(plan_id), number)


@router.post("/api/plans/{plan_id}/weeks/{number}/send-to-garmin")
def send_week(plan_id: str, number: int, changed_only: bool = False):
    provider, answer = pusher()
    return answer or delivery.send_week(provider, lifecycle.require_active(plan_id), number, changed_only)
