---
name: analysis
description: How to answer a question from the athlete's data with code. Use for any analysis run.
---

# Doing an analysis

## Load

```python
import json, pathlib
ws = pathlib.Path("<workspace path from the question>")
index = [json.loads(line) for line in (ws / "activities/index.jsonl").read_text().splitlines() if line.strip()]
wellness = [json.loads(line) for path in sorted((ws / "wellness").glob("*.jsonl")) for line in path.read_text().splitlines() if line.strip()]
plan = json.loads((ws / "plan/plan.json").read_text()) if (ws / "plan/plan.json").exists() else None
```

One activity in full is `activities/<key>.json`; its recording is fetched with `openstride source streams <key>` and read from the path that prints (`columns`, one list per channel, same length). Fetch recordings only for the activities the question needs. Which planned session an activity counts as: `openstride activities` gives `session_id` per activity.

## Method

- State the question as something measurable before computing. "Is my easy pace drifting faster" becomes pace on easy-typed runs over time, and pace at a fixed heart-rate band to separate fitness from effort.
- Compare like with like: same session type, similar duration; mind hills (altitude), heat where there is a temperature channel, and treadmill or track runs.
- Exclude or flag bad data rather than averaging it in: activities with `partial: true`, missing heart rate, GPS gaps, the first minutes of heart-rate lag.
- With few data points, say so and give the numbers rather than a trend line. A handful of runs does not make a trend.
- Check the headline number a second way.

## Deliver

`report/report.md`: the answer in the first paragraph, then a short table or the key numbers, a chart if it shows something a number does not, then method and limits. Save scripts and derived tables in `report/`. Charts for the report: matplotlib, saved as PNG in `report/`, linked with a relative path.

For the athlete's stream, at most two cards: `openstride show chart --from chart.json` (line or bar, up to 6 series and 1000 points) or `openstride show table --from table.json` (up to 200 rows). Title them with the finding, not the method.

Your last message summarises for the coach.
