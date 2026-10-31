import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app import storage
from app.generator import GenerationError, generate_plan
from app.models import PlanFile, PlanRequest

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    yield


app = FastAPI(title="OpenStride", lifespan=lifespan)


# Plain def: FastAPI runs it in a thread pool, so the long API call doesn't block the server.
@app.post("/api/plan")
def create_plan(request: PlanRequest) -> PlanFile:
    try:
        return generate_plan(request)
    except GenerationError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/plans")
def get_plans() -> list[dict]:
    return storage.list_plans()


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str) -> PlanFile:
    plan_file = storage.load_plan(plan_id)
    if plan_file is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan_file


# Mounted last so it never shadows API routes; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True))
