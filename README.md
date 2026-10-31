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
