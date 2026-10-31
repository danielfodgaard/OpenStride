"""Garmin endpoints. Stubs until Track C lands, so the UI can be built against them."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


def not_configured() -> JSONResponse:
    return JSONResponse({"error": "garmin_not_configured"}, status_code=501)


@router.get("/api/garmin/status")
def garmin_status():
    return not_configured()


@router.post("/api/garmin/sync")
def garmin_sync():
    return not_configured()


@router.get("/api/plans/{plan_id}/sessions/{week}/{index}/workout.fit")
def workout_fit(plan_id: str, week: int, index: int):
    return not_configured()


@router.post("/api/plans/{plan_id}/sessions/{week}/{index}/send-to-garmin")
def send_session(plan_id: str, week: int, index: int):
    return not_configured()


@router.post("/api/plans/{plan_id}/weeks/{number}/send-to-garmin")
def send_week(plan_id: str, number: int):
    return not_configured()
