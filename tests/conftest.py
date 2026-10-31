import os
import time

import pytest

# Tests assume Singapore local dates, so they give the same answer on any machine.
os.environ["TZ"] = "Asia/Singapore"
time.tzset()

# Before any app import, so a developer's .env can't reach the tests.
os.environ |= {
    "GARMIN_MODE": "off", "GARMIN_SYNC": "off", "GARMIN_PUSH": "off", "GARMIN_PERSONAL_API_KEY": "",
    "GARMIN_SYNC_MINUTES": "0", "OPENSTRIDE_PASSWORD": "",
}


@pytest.fixture(autouse=True)
def no_real_api_key(monkeypatch):
    # main.py loads .env, and no test may spend the developer's real tokens.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
