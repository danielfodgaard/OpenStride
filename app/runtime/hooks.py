"""The write boundary, checked before each tool call runs.

File tools are checked exactly. A shell command can't be: it is screened for the ways a command writes,
moves or deletes a file, for git commands that change history, and for anything that reaches the network
or the server's secrets. What a script does inside the shell is beyond this check; the OS sandbox and the
check after every turn (workspace.enforce) are what make the boundary hold.
"""
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from app import workspace

GIT_READS = {
    "log", "show", "diff", "status", "blame", "grep", "ls-files", "ls-tree", "cat-file", "rev-parse", "rev-list",
    "shortlog", "describe", "whatchanged", "help", "version", "--version", "--help",
}
NETWORK = {"curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "ftp", "socat", "nmap", "dig", "nslookup", "ping"}
WRITES_ALL = {"rm", "rmdir", "mv", "truncate", "shred", "chmod", "chown", "chgrp", "touch", "mkdir", "ln", "unlink", "tee"}
WRITES_LAST = {"cp", "install", "rsync"}
IN_PLACE = {"sed": ("-i", "--in-place"), "perl": ("-i", "-pi", "-ni"), "awk": ("inplace",), "gawk": ("inplace",)}
WRAPPERS = {"sudo", "command", "exec", "time", "nohup", "nice", "env", "xargs", "timeout", "stdbuf"}
SEPARATORS = re.compile(r"\|\||&&|[;|\n&]|\$\(|`|\)")
REDIRECT = re.compile(r"(?:\d|&)?>{1,2}\|?\s*(\"[^\"]+\"|'[^']+'|[^\s;|&<>()]+)")
HARMLESS_TARGETS = ("/dev/null", "/dev/stdout", "/dev/stderr", "&1", "&2")
# The server's own address. Its approve route is the athlete's; nothing in a shell command needs to name it.
SERVER = re.compile(r"\blocalhost\b|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|/api/(proposals|messages|turn|sync)", re.I)
ENVIRONMENT = re.compile(r"ANTHROPIC|API_KEY|\benviron\b|\bprintenv\b|\bexport\s+-p\b|\bdeclare\s+-x\b|\bcompgen\s+-[ev]", re.I)
TIER_REASON = {
    "gated": "plan/ changes only when the athlete approves a proposal. Write the new weeks to a file in scratch/ and run `openstride plan draft --from <file>`.",
    "recorded": "This is the record from the provider; no agent writes it. A correction goes in notes/<activity>/, or is made on the provider and comes back with the next sync.",
    "server": "This path is kept by the server. Use the `openstride` command that does what you want.",
}


@dataclass(frozen=True)
class Policy:
    role: str  # coach or analyst
    workspace: Path
    cwd: Path
    scratch: Path | None = None  # the analyst's own folder, the only place it writes
    secrets: tuple[str, ...] = ()  # path fragments no command may mention
    web: bool = False


def resolve(path: str, cwd: Path) -> Path:
    candidate = Path(path.strip().strip("\"'")).expanduser()
    return (candidate if candidate.is_absolute() else cwd / candidate).resolve()


def inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def write_problem(policy: Policy, path: str, cwd: Path | None = None) -> str | None:
    """Why this agent may not write `path`, or None when it may."""
    target = resolve(path, cwd or policy.cwd)
    if policy.role == "analyst":
        if policy.scratch and inside(target, policy.scratch):
            return None
        return "The analyst writes only in its own folder. Put the report in report/ there; the server files it under reports/."
    if not inside(target, policy.workspace):
        return "That is outside the workspace. Everything you write lives inside it."
    kind = workspace.tier(target.relative_to(policy.workspace.resolve()).as_posix())
    return TIER_REASON.get(kind)


def read_problem(policy: Policy, path: str) -> str | None:
    target = resolve(path, policy.cwd)
    if inside(target, policy.workspace) or (policy.scratch and inside(target, policy.scratch)):
        return None
    return "That is outside the workspace. Everything about the athlete is inside it."


def segments(command: str) -> list[str]:
    return [part.strip() for part in SEPARATORS.split(command) if part.strip()]


def words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def bare(tokens: list[str]) -> list[str]:
    """The command without `VAR=value` prefixes and wrappers such as sudo or xargs."""
    index = 0
    while index < len(tokens) and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]) or Path(tokens[index]).name in WRAPPERS or (index and tokens[index].startswith("-") and Path(tokens[0]).name in WRAPPERS)):
        index += 1
    return tokens[index:]


def git_problem(tokens: list[str]) -> str | None:
    rest, index = tokens[1:], 0
    while index < len(rest) and rest[index].startswith("-"):
        index += 2 if rest[index] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    subcommand = rest[index] if index < len(rest) else "help"
    if subcommand in GIT_READS:
        return None
    return (
        f"`git {subcommand}` changes the repository, and only the server commits. Read history with git log, show and diff; "
        "to undo something you wrote, use `openstride restore <path> --from <commit>`."
    )


def bash_problem(policy: Policy, command: str) -> str | None:
    lowered = command.lower()
    for fragment in policy.secrets:
        if fragment and fragment.lower() in lowered:
            return "That reaches for the server's own files or secrets, which are not part of the workspace."
    if re.search(r"(^|[\s/\"'=])\.env(\b|$)", command) or "/proc/" in command or ENVIRONMENT.search(command):
        return "That reaches for the server's own files or secrets, which are not part of the workspace."
    if SERVER.search(command):
        return "The server is reached only through the `openstride` command. Approving a plan is the athlete's, on the card."
    cwd = policy.cwd
    for segment in segments(command):
        tokens = bare(words(segment))
        if not tokens:
            if Path((words(segment) or ["x"])[0]).name in ("env", "set"):  # a bare `env` or `set` prints the environment
                return "That reaches for the server's own files or secrets, which are not part of the workspace."
            continue
        if tokens == ["set"]:
            return "That reaches for the server's own files or secrets, which are not part of the workspace."
        name = Path(tokens[0]).name
        arguments = [t for t in tokens[1:] if not t.startswith("-")]
        if name == "cd" and arguments:
            cwd = resolve(arguments[0], cwd)
            continue
        if name in NETWORK:
            return "There is no network in the workspace. The provider is reached with `openstride source …`, and research with `openstride analyse --web`."
        if name == "git":
            problem = git_problem(tokens)
            if problem:
                return problem
        if name == "openstride" and tokens[1:3] == ["plan", "apply"]:
            return "Only the athlete's Approve applies a plan. Draft the change and explain it; the card has the button."
        targets: list[str] = []
        if name in WRITES_ALL:
            targets = arguments
        elif name in WRITES_LAST and arguments:
            targets = arguments[-1:]
        elif name == "dd":
            targets = [t.split("=", 1)[1] for t in tokens[1:] if t.startswith("of=")]
        elif name in IN_PLACE and any(flag.startswith(marks) for flag in tokens[1:] for marks in IN_PLACE[name]):
            targets = [t for t in arguments if (cwd / t).exists()]
        for match in REDIRECT.finditer(segment):
            target = match.group(1).strip("\"'")
            if target not in HARMLESS_TARGETS and not target.startswith("&"):
                targets.append(target)
        for target in targets:
            if "$" in target or "*" in target and not (cwd / target.split("*")[0]).exists():
                continue  # can't be resolved here; the check after the turn catches a protected write
            problem = write_problem(policy, target, cwd)
            if problem:
                return problem
    return None


def check(policy: Policy, tool: str, tool_input: dict) -> str | None:
    """The reason to refuse this tool call, or None to let it run."""
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return write_problem(policy, str(tool_input.get("file_path") or tool_input.get("notebook_path") or ""))
    if tool == "Read":
        return read_problem(policy, str(tool_input.get("file_path") or ""))
    if tool in ("Glob", "Grep"):
        return read_problem(policy, str(tool_input["path"])) if tool_input.get("path") else None
    if tool == "Bash":
        return bash_problem(policy, str(tool_input.get("command") or ""))
    if tool in ("WebSearch", "WebFetch"):
        return None if policy.web else "This run has no web access. Research needs `openstride analyse --web`."
    if tool in ("Skill", "TodoWrite", "Agent", "Task"):
        return None
    return f"The {tool} tool is not part of this workspace."
