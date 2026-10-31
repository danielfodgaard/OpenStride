"""Garmin endpoints. In mode `off` these say so, except what needs no Garmin."""
from dataclasses import asdict

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app import lifecycle, workout_fit
from app.garmin import delivery, sync
from app.garmin.provider import GarminError, current_mode, make_provider

router = APIRouter()


def not_configured(error: str = "garmin_not_configured") -> JSONResponse:
    return JSONResponse({"error": error}, status_code=501)


def refused(error: GarminError) -> JSONResponse:
    return JSONResponse({"error": "garmin_error", "detail": str(error)}, status_code=502)


def pusher():
    """The provider, or the response to send instead."""
    if current_mode() == "off":
        return None, not_configured()
    provider = make_provider()
    return (provider, None) if provider.can_push_workouts() else (None, not_configured("push_not_supported"))


@router.get("/api/garmin/status")
def garmin_status():
    return asdict(make_provider().status())


@router.post("/api/garmin/sync")
def garmin_sync():
    return sync.sync() if current_mode() != "off" else not_configured()


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
    try:
        return delivery.send_session(provider, plan_file, week, index)
    except GarminError as error:
        return refused(error)


@router.get("/api/plans/{plan_id}/weeks/{number}/garmin")
def week_status(plan_id: str, number: int):
    return delivery.week_summary(lifecycle.require_plan(plan_id), number)


@router.post("/api/plans/{plan_id}/weeks/{number}/send-to-garmin")
def send_week(plan_id: str, number: int, changed_only: bool = False):
    provider, answer = pusher()
    return answer or delivery.send_week(provider, lifecycle.require_active(plan_id), number, changed_only)
