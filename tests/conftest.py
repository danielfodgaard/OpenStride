import pytest


@pytest.fixture(autouse=True)
def no_real_api_key(monkeypatch):
    # main.py loads .env, and no test may spend the developer's real tokens.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
