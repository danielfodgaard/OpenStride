import json
from datetime import date

from app.models import DAYS, Paces, SavedRequest

SYSTEM_PROMPT = """You are an experienced running coach creating a periodised training plan.

Rules:
- The paces below are a reference computed from the athlete's recent race; you may adjust them if the athlete context justifies it, and say why in the summary.
- Produce exactly the requested number of weeks, numbered from 1.
- Schedule sessions only on available days. A week has at most runs_per_week runs (the race counts as a run), strength_sessions_per_week strength sessions and cross_sessions_per_week cross-training sessions. At most one run per day; a strength or cross session may share a day with a run.
- Never put two hard runs (tempo, intervals, long) on consecutive days, including Sunday to Monday across weeks.
- The long run goes on long_run_day.
- Start weekly volume near current_weekly_km. Increase it at most about 10% per week, with a lighter week every 3-4 weeks. Taper for the final 1-3 weeks depending on race distance.
- The final week contains exactly one race session, on the race day, even if that is not an available day. Nothing is scheduled after it. No other week contains a race session.
- If the goal VDOT exceeds the current VDOT by more than about 2 points per 8 weeks available, add a warning. Otherwise warnings may be empty.
- Use 0 for distance_km or duration_min when not applicable. steps is empty for simple steady runs; repeat is 1 for a single block.
- planned_km counts running only.
- Every session has a modality: "run" for running types, "strength" for type strength, "cross" for type cross_training.
- Strength sessions go on hard run days or rest days per the athlete's note, never the day before a long run or race. They last 20-40 minutes with 4-8 exercises, each a Step with repeat = sets, reps, load (for example "bodyweight", "20 kg" or "RPE 7") and description = exercise name, using only the athlete's equipment. Their distance_km is 0 and their target_pace is "".
- Cross-training replaces easy volume, not quality sessions. It uses the athlete's cross_training sport, is duration-based with distance_km 0, and its target_pace holds the target effort, for example "RPE 4".
- For running steps, reps is 0 and load is "".
- Keep descriptions short.
- Your notes about the athlete come from earlier plans and conversations. Let them shape the plan (injury history, what worked, agreements) and say so in the summary when they do. When the request conflicts with them (for example a volume that injured the athlete before), follow the request and add a warning."""


def build_user_prompt(request: SavedRequest, paces: Paces, starts: list[date], notes: str) -> str:
    return f"""Runner request:
{request.model_dump_json(indent=2)}

Reference paces:
{paces.model_dump_json(indent=2)}

Number of weeks: {len(starts)}
Week start dates: {json.dumps([d.isoformat() for d in starts])}
Race date: {request.race_date.isoformat()}, a {DAYS[request.race_date.weekday()]}

{notes}"""
