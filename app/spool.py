"""How the `openstride` CLI inside an agent's shell reaches the server: files in a folder the agent may write.

The shell has no network and, under the OS sandbox, no sockets either. So the CLI drops a request file
in its turn's spool folder and waits for the answer file; the server watches the folders of the turns
that are running and hands each request to the agent API. Nothing but the filesystem is involved, which
is what every sandbox and a shared container volume have in common.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
POLL_S = 0.05
STALE_S = 900

_folders: dict[Path, int] = {}


def register(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    _folders[folder] = _folders.get(folder, 0) + 1


def unregister(folder: Path) -> None:
    if folder in _folders:
        _folders[folder] -= 1
        if _folders[folder] <= 0:
            del _folders[folder]


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


async def answer(client: httpx.AsyncClient, folder: Path, name: str, request: dict) -> None:
    try:
        response = await client.request(
            request.get("method", "POST"), request.get("path", "/"), json=request.get("body"),
            headers={"X-OpenStride-Token": str(request.get("token") or "")}, timeout=None,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"error": response.text[:500]}
        result = {"status": response.status_code, "body": body}
    except Exception as error:  # the CLI must get an answer, whatever went wrong here
        log.exception("An agent's request failed")
        result = {"status": 500, "body": {"error": f"The server failed on this request: {type(error).__name__}."}}
    if folder.exists():
        write_atomic(folder / f"{name}.res.json", json.dumps(result))


async def watch(app) -> None:
    """Serve requests from every registered folder until cancelled."""
    tasks: set[asyncio.Task] = set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://openstride") as client:
        while True:
            for folder in list(_folders):
                try:
                    names = [n for n in os.listdir(folder) if n.endswith((".req.json", ".res.json"))]
                except OSError:
                    continue
                for filename in names:
                    path = folder / filename
                    try:
                        if filename.endswith(".res.json"):
                            if time.time() - path.stat().st_mtime > STALE_S:
                                path.unlink(missing_ok=True)  # an answer nobody came back for
                            continue
                        request = json.loads(path.read_text())
                        path.unlink()
                    except (OSError, ValueError):
                        continue
                    task = asyncio.create_task(answer(client, folder, filename.removesuffix(".req.json"), request))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            await asyncio.sleep(POLL_S)


def ask(folder: Path, token: str, method: str, path: str, body: dict | None, timeout: float) -> tuple[int, dict]:
    """The CLI's side: write the request, wait for the answer. Raises TimeoutError when none comes."""
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    write_atomic(folder / f"{name}.req.json", json.dumps({"method": method, "path": path, "body": body, "token": token}))
    answer_path = folder / f"{name}.res.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if answer_path.exists():
            try:
                result = json.loads(answer_path.read_text())
            except ValueError:
                time.sleep(POLL_S)
                continue
            answer_path.unlink(missing_ok=True)
            return result["status"], result["body"]
        time.sleep(POLL_S)
    (folder / f"{name}.req.json").unlink(missing_ok=True)
    raise TimeoutError
