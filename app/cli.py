"""The `openstride` console script: run the server, `openstride backup <folder>` or `openstride garmin-disconnect`."""
import argparse
import sys
from pathlib import Path

import uvicorn

from app import config, storage
from app.garmin import provider


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
    commands.add_parser("garmin-disconnect", help="delete the saved Garmin login token")
    arguments = parser.parse_args()
    if arguments.command == "backup":
        print(storage.write_backup(arguments.folder, arguments.include_secrets))
    elif arguments.command == "garmin-disconnect":
        provider.disconnect()
        print("Garmin login token deleted. Sending is off until you log in again.")
    else:
        serve()
