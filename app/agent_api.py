"""The server's door for agents: what the `openstride` CLI calls from inside an agent's shell.

It is not on the network: requests arrive as files in the running turn's spool folder (app/spool.py),
so it works when the shell is sandboxed with no network and no sockets. It has no route that approves
or applies a plan. Each call carries the token its agent was started with.
"""
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import analyst, ops
from app.hub import hub

COACH_ONLY = {"source_upload", "source_wellness", "source_log", "source_push", "source_sync", "commit", "restore", "analyse"}


class ShowBody(BaseModel):
    card: str
    args: dict = {}
    data: dict | None = None


class AskBody(BaseModel):
    question: str
    options: list[str]


class DraftBody(BaseModel):
    payload: dict
    new: bool = False


class CallBody(BaseModel):
    args: dict = {}


class AnalyseBody(BaseModel):
    question: str = ""
    web: bool = False
    effort: str = ""
    context: str = ""
    run: str = ""  # an earlier run to keep waiting for
    wait: float = 540


def create() -> FastAPI:
    app = FastAPI(title="OpenStride agent API", docs_url=None, redoc_url=None)

    def role(token: str | None) -> str:
        found = hub.role_of(token or "")
        if found is None:
            raise HTTPException(401, "This command runs inside an agent's turn. Outside one, use the app.")
        return found

    @app.exception_handler(ops.OpError)
    async def op_error(request: Request, error: ops.OpError):
        return JSONResponse({"error": str(error), "problems": error.problems}, status_code=422)

    @app.get("/whoami")
    def whoami(x_openstride_token: str | None = Header(None)) -> dict:
        return {"role": role(x_openstride_token)}

    @app.post("/show")
    def show(body: ShowBody, x_openstride_token: str | None = Header(None)) -> dict:
        return ops.show(role(x_openstride_token), body.card, body.args, body.data)

    @app.post("/ask")
    def ask(body: AskBody, x_openstride_token: str | None = Header(None)) -> dict:
        return ops.ask(role(x_openstride_token), body.question, body.options)

    @app.post("/plan/draft")
    def draft(body: DraftBody, x_openstride_token: str | None = Header(None)) -> dict:
        return ops.draft(role(x_openstride_token), body.payload, body.new)

    @app.post("/call/{name}")
    async def call(name: str, body: CallBody, x_openstride_token: str | None = Header(None)) -> dict:
        who = role(x_openstride_token)
        allowed = {"source_capabilities", "source_check", "source_streams", "reports", "usage"} | COACH_ONLY - {"analyse"}
        if name not in allowed:
            raise HTTPException(404, f"There is no operation {name}.")
        if name in COACH_ONLY and who != "coach":
            raise ops.OpError("The analyst reads and reports. This is the coach's to do.")
        return await run_in_threadpool(lambda: getattr(ops, name)(who, **body.args))

    @app.post("/analyse")
    async def analyse(body: AnalyseBody, x_openstride_token: str | None = Header(None)) -> dict:
        if role(x_openstride_token) != "coach":
            raise ops.OpError("The analyst doesn't start another analyst.")
        if body.run:
            run = await analyst.wait(body.run, body.wait)
        else:
            if not body.question.strip():
                raise ops.OpError("Give the analyst a question.")
            run = await analyst.wait(analyst.start(hub, body.question.strip(), body.web, body.effort, body.context)["id"], body.wait)
        if run["state"] == "unknown":
            raise ops.OpError(f"There is no analyst run {body.run!r}.")
        if run["state"] == "running":
            return {"run": run["id"], "state": "running", "next": f"Still working. Wait again with `openstride analyse --run {run['id']}`, or end your turn: you get a brief when it is done."}
        return {
            "run": run["id"], "state": run["state"], "summary": run["summary"], "error": run["error"],
            "report": f"reports/{run['report']}/report.md" if run["report"] else "",
            "next": "Tell the athlete what was found in your own words. `openstride show report " + run["report"] + "` puts the report in front of them." if run["report"] else "",
        }

    return app
