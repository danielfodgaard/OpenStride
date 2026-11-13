"""The Claude Agent SDK as the harness: its loop, shell and file tools, skills, session resume and
compaction, run as a subprocess of the server with the workspace as its working directory.

The boundary is set three ways: permission rules and the OS sandbox (when the machine has one) in the
harness's settings, and a PreToolUse hook that runs hooks.check before every tool call.
"""
import asyncio
import functools
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app import config, workspace
from app.runtime import hooks
from app.runtime.base import TurnRequest

log = logging.getLogger(__name__)

COACH_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
WEB_TOOLS = ["WebSearch", "WebFetch"]


# The harness runs each shell command under bubblewrap in a user namespace. These are that call in small:
# the strong form gives the shell its own /proc; the weak form, for a container that may not mount one, does not.
PROBE = ["bwrap", "--unshare-user", "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-net", "--unshare-pid"]


@functools.cache
def sandbox_probe() -> tuple[str, str]:
    """What the OS sandbox can do here: ("strong" | "weak" | "", why not).

    Seatbelt on a Mac is always there. On Linux it takes bubblewrap, socat and leave to make a namespace,
    which a default Docker container does not have.
    """
    if platform.system() == "Darwin":
        return "strong", ""
    if platform.system() != "Linux":
        return "", f"there is no OS sandbox for {platform.system()}"
    missing = [name for name in ("bwrap", "socat") if not shutil.which(name)]
    if missing:
        return "", f"{' and '.join(missing)} not installed"
    reason = ""
    for level, extra in (("strong", ["--proc", "/proc"]), ("weak", [])):
        try:
            probe = subprocess.run([*PROBE, *extra, "true"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            return "", f"bubblewrap could not run: {error}"
        if probe.returncode == 0:
            return level, ""
        reason = reason or (probe.stderr.strip().splitlines() or ["bubblewrap failed"])[-1]
    return "", reason


def sandbox_level() -> str:
    """The form of sandbox the agents' shells run under: strong, weak, or "" for none."""
    mode = config.settings.sandbox
    if mode == "off":
        return ""
    if mode == "on":
        return "strong"
    if mode == "weak":
        return "weak"
    return sandbox_probe()[0]


def sandbox_on() -> bool:
    return bool(sandbox_level())


def absolute(path: Path) -> str:
    """A path as permission rules and sandbox settings write an absolute one."""
    return "/" + str(path.resolve())


def harness_settings(request: TurnRequest) -> dict:
    """Permission rules for the file tools, and the sandbox for the shell."""
    policy = request.policy
    state = workspace.state()
    protected = [policy.workspace / p for p in workspace.PROTECTED]
    deny_write = [absolute(p) for p in protected] if policy.role == "coach" else [absolute(policy.workspace)]
    deny_read = [absolute(state), absolute(Path(__file__).parent.parent.parent / ".env")]
    rules = [f"Edit({path}/**)" for path in deny_write] + [f"Edit({path})" for path in deny_write]
    rules += [f"Read({path}/**)" for path in deny_read[:1]] + [f"Read({deny_read[1]})"]
    settings: dict = {"permissions": {"deny": rules}}
    if sandbox_on():
        settings["sandbox"] = {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "failIfUnavailable": config.settings.sandbox in ("on", "weak"),
            # In a container the shell can't be given its own /proc: weaker, and far better than none.
            "enableWeakerNestedSandbox": sandbox_level() == "weak",
            "network": {"allowedDomains": []},  # none: the CLI reaches the server through files (app/spool.py)
            "filesystem": {
                "allowWrite": [absolute(policy.scratch or policy.workspace)],
                "denyWrite": deny_write,
                "denyRead": deny_read,
            },
        }
    return settings


def environment(request: TurnRequest) -> dict:
    """What the harness and the agent's shell see. Secrets are blanked; the CLI finds the server by its spool folder and the turn's token."""
    bin_dir = str(Path(sys.executable).parent)
    env = {name: "" for name in config.SECRET_ENV}
    env |= {
        "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
        "OPENSTRIDE_WORKSPACE": str(request.policy.workspace),
        "OPENSTRIDE_TURN": request.token,
        "OPENSTRIDE_DATA_DIR": "",
        "CLAUDE_CONFIG_DIR": str(workspace.state() / "claude"),  # sessions live with the data, so they survive a container
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*",  # the agent's own git log, on a bind mount
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
    }
    if sandbox_level() == "strong":
        # Keeps the Anthropic key out of the shell's environment. The harness does this only under the full sandbox:
        # in the container form the shell can read /proc, where the key is visible anyway. There, and with no sandbox,
        # the tool-call check refuses commands that read the environment (hooks.ENVIRONMENT), and there is no network
        # in the container form to send a key anywhere.
        env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    return env | request.env


class ClaudeSdk:
    name = "claude"

    async def run(self, request: TurnRequest):
        from claude_agent_sdk import (
            AgentDefinition, AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage, TextBlock, ToolUseBlock, query,
        )
        from claude_agent_sdk.types import StreamEvent

        policy = request.policy
        denied: list[str] = []
        stderr: list[str] = []

        def keep(line: str) -> None:
            log.debug("harness: %s", line)
            stderr.append(line.rstrip())
            del stderr[:-12]

        async def pre_tool(hook_input, tool_use_id, context):
            reason = hooks.check(policy, hook_input.get("tool_name", ""), hook_input.get("tool_input") or {})
            if reason is None:
                return {}
            denied.append(reason)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}

        env = environment(request)
        tools = COACH_TOOLS + (WEB_TOOLS if policy.web else [])
        agents = None
        if policy.role == "analyst":
            # Bulk reading on the cheaper model, so the analyst's own context holds conclusions, not file dumps.
            tools = tools + ["Agent"]
            agents = {"reader": AgentDefinition(
                description="Reads many activity files or recordings and returns the numbers asked for. Use it for bulk reading that would fill your context.",
                prompt="You read a runner's training data files and return exactly the numbers you were asked for, computed with code, with the file each came from. You write only in the current folder.",
                tools=COACH_TOOLS, model=request.helper_model,
            )}
        options = ClaudeAgentOptions(
            cwd=str(policy.cwd),
            system_prompt=request.system_prompt,
            tools=tools,
            allowed_tools=tools,
            permission_mode="dontAsk",
            setting_sources=["project"],
            skills="all",
            resume=request.session_id,
            model=request.model,
            effort=request.effort,
            include_partial_messages=True,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool], on_failure="block")]},
            agents=agents,
            env=env,
            add_dirs=[str(policy.workspace)] if policy.role == "analyst" else [],
            settings=json.dumps(harness_settings(request)),
            stderr=keep,
        )
        result = {"session_id": request.session_id, "usage": {}, "cost_usd": 0.0, "error": "", "turns": 0}
        try:
            async with asyncio.timeout(request.max_seconds):
                async for message in query(prompt=request.prompt, options=options):
                    if isinstance(message, StreamEvent):
                        if message.parent_tool_use_id:
                            continue
                        event = message.event
                        if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                            yield ("delta", event["delta"].get("text", ""))
                    elif isinstance(message, AssistantMessage):
                        if message.parent_tool_use_id:
                            continue
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                yield ("text", block.text)
                            elif isinstance(block, ToolUseBlock):
                                yield ("tool", {"name": block.name, "input": block.input})
                        while denied:
                            yield ("denied", denied.pop(0))
                    elif isinstance(message, ResultMessage):
                        result |= {
                            "session_id": message.session_id, "usage": message.usage or {}, "cost_usd": message.total_cost_usd or 0.0,
                            "turns": message.num_turns, "model_usage": message.model_usage or {},
                        }
                        if message.is_error:
                            result["error"] = "; ".join(message.errors or []) or message.result or message.subtype
        except TimeoutError:
            result["error"] = f"The turn was stopped after {request.max_seconds // 60} minutes."
        except Exception as error:  # the harness failed to start or died: report it, don't take the server down
            log.exception("The agent runtime failed")
            detail = " | ".join(line for line in stderr if line.strip())[-600:]
            result["error"] = f"The agent could not run: {type(error).__name__}: {str(error).splitlines()[0]}" + (f" ({detail})" if detail else "")
        if result["error"] and re.search(r"api key|authenticat|unauthori[sz]ed|\b401\b|/login", result["error"], re.I):
            result["error"] = "The agent has no working Anthropic credentials. Set ANTHROPIC_API_KEY in .env and restart. (" + result["error"][:200] + ")"
        yield ("result", result)
