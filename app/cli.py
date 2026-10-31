"""The `openstride` console script: run the server, or `openstride backup <folder>`."""
import argparse
import sys
from pathlib import Path

import uvicorn

from app import config, storage


def serve() -> None:
    problem = config.open_server_problem()
    if problem:  # the app's lifespan refuses too; here it is one clean line and no traceback
        sys.exit(problem)
    uvicorn.run("app.main:app", host=config.settings.host, port=config.settings.port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="openstride")
    commands = parser.add_subparsers(dest="command")
    backup = commands.add_parser("backup", help="write a zip of the data folder")
    backup.add_argument("folder", type=Path)
    backup.add_argument("--include-secrets", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "backup":
        print(storage.write_backup(arguments.folder, arguments.include_secrets))
    else:
        serve()
