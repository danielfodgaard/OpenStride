"""The `openstride` console script."""
import sys

import uvicorn

from app import config


def main() -> None:
    problem = config.open_server_problem()
    if problem:  # the app's lifespan refuses too; here it is one clean line and no traceback
        sys.exit(problem)
    uvicorn.run("app.main:app", host=config.settings.host, port=config.settings.port)
