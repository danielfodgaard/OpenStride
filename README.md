# OpenStride

Open-source running plan generator. Fill in a form; the backend computes your pace zones with the Daniels–Gilbert formulas, asks Claude for a periodised plan, validates it and saves it as JSON.

## Setup

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    cp .env.example .env   # then set ANTHROPIC_API_KEY

## Run

    .venv/bin/uvicorn app.main:app

Open http://127.0.0.1:8000.

## Test

    .venv/bin/pytest

The sample plan needs no server, key or network: open `static/index.html` in a browser and use "Load plan file" with `examples/sample_plan.json`. The API server refuses to start without `ANTHROPIC_API_KEY`.

`examples/sample_request.json` holds a fixed race date, so it falls outside the allowed 3–24 week window after 20 December 2026 (the form works out its own date).

## How paces are computed

`app/vdot.py` applies the Daniels–Gilbert formulas to your recent race. Claude only schedules the sessions; it must copy these paces exactly, and `app/validate.py` rejects any others.

## Cost per plan

Every plan file records its real token usage in `meta.usage`, including any retry. Cost is `input_tokens × $2/M + output_tokens × $10/M` at Claude Sonnet 5.5 list prices.

Estimate, not yet measured: a 10-week plan is about 17,000 characters of JSON, roughly 6,000 output tokens plus 1,500 input, so about **$0.06**, more with any thinking tokens or a retry. Replace this with a figure from a real `meta.usage`.

## License

MIT
