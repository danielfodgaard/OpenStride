import json
from datetime import date

from app.models import DAYS, Paces, SavedRequest

SYSTEM_PROMPT = """You are an experienced running coach creating a periodised training plan.

Rules:
- The paces below are a reference computed from the athlete's recent race; you may adjust them if the athlete context justifies it, and say why in the summary.
- Produce exactly the requested number of weeks, numbered from 1.
- Schedule sessions only on available days, at most runs_per_week per week, one per day. Never put two hard sessions (tempo, intervals, long) on consecutive days, including Sunday to Monday across weeks.
- The long run goes on long_run_day.
- Start weekly volume near current_weekly_km. Increase it at most about 10% per week, with a lighter week every 3-4 weeks. Taper for the final 1-3 weeks depending on race distance.
- The final week contains exactly one race session, on the race day, even if that is not an available day. Nothing is scheduled after it. No other week contains a race session.
- If the goal VDOT exceeds the current VDOT by more than about 2 points per 8 weeks available, add a warning. Otherwise warnings may be empty.
- Use 0 for distance_km or duration_min when not applicable. steps is empty for simple steady runs; repeat is 1 for a single block.
- Keep descriptions short."""


def build_user_prompt(request: SavedRequest, paces: Paces, starts: list[date]) -> str:
    return f"""Runner request:
{request.model_dump_json(indent=2)}

Reference paces:
{paces.model_dump_json(indent=2)}

Number of weeks: {len(starts)}
Week start dates: {json.dumps([d.isoformat() for d in starts])}
Race date: {request.race_date.isoformat()}, a {DAYS[request.race_date.weekday()]}"""
