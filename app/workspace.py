"""The workspace: the athlete's data as files in a git repository, and who may write which of them.

Three tiers. Recorded data (what the watch and the provider say) is written only by the connector.
The plan is written only through the approval gate. Everything else the agent authors freely.
The server commits its own writes at once, so anything else that differs from the last commit in a
protected path was written by an agent, and is put back.
"""
import fcntl
import os
import shutil
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app import config

GATED = ("plan",)
RECORDED = ("activities", "wellness", "athlete/profile.yaml")
SERVER_ONLY = (".git", ".claude", ".gitignore", "GUIDE.md", "proposals", "reports")
PROTECTED = GATED + RECORDED + SERVER_ONLY
TRACKED_PROTECTED = tuple(p for p in PROTECTED if p != ".git")
AUTHORED_DIRS = ("athlete", "notes", "journal", "journal/weeks", "scratch")
GITIGNORE = "scratch/\n*.tmp\n__pycache__/\nactivities/*.streams.json\n"
GIT_IDENTITY = ("-c", "user.name=OpenStride", "-c", "user.email=openstride@localhost", "-c", "commit.gpgsign=false")
GUIDE_DIR = Path(__file__).parent / "guide"

DEFAULT_SETTINGS = {
    "units": "km",
    "models": {"main": "claude-opus-5-5", "analyst": "claude-opus-5-5", "helper": "claude-sonnet-5-5"},
    "effort": {"main": "high", "triggers": "medium", "analyst": "high"},
    "sync_minutes": 15,
    "quiet_hours": {"from": "21:30", "to": "07:00"},
    "triggers": {
        "new_activity": True,
        "week_end": True,
        "plan_end": True,
        "calendar_drift": True,
        "morning_brief": False,
        "morning_brief_time": "07:30",
        "wellness_signal": False,
        "block_review": False,
    },
    "budget": {"monthly_usd": 0},  # 0 means no cap
}

MEMORY_TEMPLATE = """# Profile
(age range, years running, races and times worth knowing, typical weekly volume over the years, other sports)

# Goals
(this plan's goal and what sits behind it, longer-term goals, what a good outcome looks like to them)

# Life and schedule
(work, family, travel patterns, sleep, usual training times, days and hours that are hard, seasons and weather)

# Injury and health
(dated: injuries, niggles and illness, how they resolved, what tends to trigger them; anything medical that matters for training)

# Response to training
(dated observations: the volume and intensity they handle, how they recover, early warning signs, what has worked and what hasn't)

# Preferences and agreements
(how they like to train and be coached, things they asked for that should hold across plans, how direct to be)

# Plan history
(one entry per plan: dates, goal, what happened, the race result, the lesson)

# Open questions
(what you still want to find out and anything to follow up on; ask one or two at a time, when they fit)
"""

_git_lock = threading.RLock()


class GitError(Exception):
    pass


def root() -> Path:
    return config.settings.workspace


def state() -> Path:
    return config.settings.state


def write_file(path: Path, content: str | bytes, mode: int = 0o644) -> None:
    """Replace atomically: a crash mid-write must not destroy the current file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        kind = "xb" if isinstance(content, bytes) else "x"
        with open(temporary, kind, opener=lambda name, flags: os.open(name, flags, mode)) as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def relative(path: str | Path, base: Path | None = None) -> str | None:
    """The path inside the workspace, or None when it points outside. Follows symlinks, so a link can't smuggle a write out."""
    base = base or root()
    candidate = Path(path)
    full = (candidate if candidate.is_absolute() else base / candidate)
    try:
        return full.resolve().relative_to(root().resolve()).as_posix()
    except ValueError:
        return None


def under(rel: str, prefixes) -> bool:
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in prefixes)


def tier(rel: str) -> str:
    if under(rel, GATED):
        return "gated"
    if under(rel, RECORDED):
        return "recorded"
    if under(rel, SERVER_ONLY):
        return "server"
    return "authored"


def git(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    with _git_lock:
        result = subprocess.run(
            ["git", *GIT_IDENTITY, *args], cwd=cwd or root(), capture_output=True, text=True,
            env=os.environ | {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        )
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


_held = threading.local()


@contextmanager
def lock():
    """One writer of server-owned files at a time, across threads and processes (the CLI and the server).

    Re-entrant: a second flock on the same file from the same process would wait for itself forever.
    """
    state().mkdir(parents=True, exist_ok=True)
    with _git_lock:
        depth = getattr(_held, "depth", 0)
        if depth:
            _held.depth = depth + 1
            try:
                yield
            finally:
                _held.depth -= 1
            return
        with open(state() / ".lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _held.depth = 1
            try:
                yield
            finally:
                _held.depth = 0
                fcntl.flock(handle, fcntl.LOCK_UN)


def head() -> str:
    return git("rev-parse", "--verify", "-q", "HEAD", check=False).strip()


def last_commit(*paths: str) -> str:
    return git("log", "-1", "--format=%H", "--", *paths, check=False).strip()


def _existing(paths) -> list[str]:
    """Paths git can take: ones with a file it tracks or could. An empty folder is not one."""
    return [p for p in paths if git("ls-files", "--cached", "--others", "--exclude-standard", "--", p, check=False).strip()]


def commit(message: str, paths=None) -> str | None:
    """Commit everything, or only `paths`. Returns the new commit, or None when nothing changed."""
    with _git_lock:
        if paths is None:
            git("add", "-A")
            if not git("diff", "--cached", "--name-only").strip():
                return None
            git("commit", "-q", "-m", message)
        else:
            paths = _existing(list(paths))
            if not paths:
                return None
            git("add", "-A", "--", *paths)
            if not git("diff", "--cached", "--name-only", "--", *paths).strip():
                return None
            git("commit", "-q", "-m", message, "--", *paths)
        return head()


def protected_head_path() -> Path:
    return state() / "protected_head"


def commit_protected(message: str, paths) -> str | None:
    """A server write to protected paths: committed at once, and remembered as the state agents can't move."""
    with _git_lock:
        made = commit(message, paths)
        write_file(protected_head_path(), head())
        return made


def changed(paths) -> list[str]:
    """Paths under `paths` that differ from the last commit, untracked files included."""
    paths = _existing(list(paths))
    if not paths:
        return []
    output = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths)
    entries, names, skip = output.split("\0"), [], False
    for entry in entries:
        if skip or not entry:
            skip = False
            continue
        names.append(entry[3:])
        skip = entry[0] in "RC"  # a rename is followed by its old name
    return names


def restore(paths, revision: str = "HEAD") -> None:
    """Put `paths` back as they are in `revision`, removing what it doesn't have."""
    with _git_lock:
        for path in paths:
            if git("ls-tree", "-r", "--name-only", revision, "--", path, check=False).strip():
                git("checkout", "-q", revision, "--", path)
            else:
                git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", path, check=False)
            git("clean", "-fdq", "--", path, check=False)
            target = root() / path
            tracked = set(git("ls-tree", "-r", "--name-only", revision, "--", path, check=False).splitlines())
            if target.is_dir():  # files the revision doesn't have but the index did
                for file in sorted(target.rglob("*")):
                    if file.is_file() and file.relative_to(root()).as_posix() not in tracked:
                        file.unlink()
            elif target.is_file() and path not in tracked:
                target.unlink()


def enforce() -> list[str]:
    """Undo anything an agent did to protected paths. Returns what was put back, for the agent's next brief."""
    with _git_lock:
        undone = changed(TRACKED_PROTECTED)
        if undone:
            restore(sorted({next(p for p in TRACKED_PROTECTED if under(name, [p])) for name in undone}))
        approved = protected_head_path().read_text().strip() if protected_head_path().exists() else ""
        if approved and approved != head():
            # Commits made behind the server's back: only the server commits, so compare with its last one.
            moved = git("diff", "--name-only", approved, "HEAD", "--", *TRACKED_PROTECTED, check=False).split()
            if moved:
                restore(sorted({next(p for p in TRACKED_PROTECTED if under(name, [p])) for name in moved}), approved)
                commit("Restored protected files changed outside the approval gate", TRACKED_PROTECTED)
                write_file(protected_head_path(), head())
                undone += moved
        return sorted(set(undone))


def log(*paths: str, limit: int = 50) -> list[dict]:
    output = git("log", f"-{limit}", "--format=%H%x1f%aI%x1f%s", "--", *paths, check=False)
    rows = [line.split("\x1f") for line in output.splitlines() if line]
    return [{"commit": commit_hash, "at": at, "message": message} for commit_hash, at, message in rows]


def show(revision: str, path: str) -> str:
    return git("show", f"{revision}:{path}")


def load_yaml(path: Path, default):
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return default
    return default if data is None else data


def dump_yaml(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def merged(defaults: dict, given) -> dict:
    """`given` over `defaults`, key by key, ignoring anything of the wrong shape."""
    result = dict(defaults)
    if not isinstance(given, dict):
        return result
    for key, value in given.items():
        if isinstance(defaults.get(key), dict):
            result[key] = merged(defaults[key], value)
        elif key not in defaults or isinstance(value, type(defaults[key])) or defaults[key] is None:
            result[key] = value
    return result


def clock_text(value) -> str | None:
    """YAML reads an unquoted 07:30 as the number 450. Give it back as a time."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 24 * 60:
        return f"{value // 60:02d}:{value % 60:02d}"
    return None


def settings() -> dict:
    """The athlete's settings over the defaults. A broken file reads as the defaults, never as a crash."""
    given = load_yaml(root() / "settings.yaml", {})
    if isinstance(given, dict):
        for section, keys in (("quiet_hours", ("from", "to")), ("triggers", ("morning_brief_time",))):
            if isinstance(given.get(section), dict):
                for key in keys:
                    given[section][key] = clock_text(given[section].get(key)) or given[section].get(key)
    return merged(DEFAULT_SETTINGS, given)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def guide_text() -> str:
    """The guide with the active provider's page: what its fields mean, in its own units."""
    from app import providers

    provider = providers.active()
    page = getattr(provider, "guide", "") if provider else "### No provider yet\n\nNo provider is connected, so activities/ and wellness/ are empty. Setup is in docs/SETUP.md: an intervals.icu API key in .env."
    return (GUIDE_DIR / "GUIDE.md").read_text().replace("{{PROVIDER}}", page)


def install_skills(target: Path, role: str) -> None:
    """A role's skills where the harness looks for them: <cwd>/.claude/skills."""
    skills = target / ".claude" / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    shutil.copytree(GUIDE_DIR / "skills" / role, skills)


def install_guide() -> None:
    """The guide and the skills come from the app, so they track its version."""
    write_file(root() / "GUIDE.md", guide_text())
    install_skills(root(), "coach")
    write_file(root() / ".gitignore", GITIGNORE)


def init() -> None:
    """Make the workspace, or bring an existing one up to this version. Safe to call at every start."""
    root().mkdir(parents=True, exist_ok=True)
    state().mkdir(parents=True, exist_ok=True)
    (state() / "secrets").mkdir(mode=0o700, exist_ok=True)
    with lock():
        if not (root() / ".git").exists():
            git("init", "-q", "-b", "main")
        for folder in AUTHORED_DIRS + ("plan", "proposals", "activities", "wellness", "reports"):
            (root() / folder).mkdir(parents=True, exist_ok=True)
        install_guide()
        if not (root() / "settings.yaml").exists():
            write_file(root() / "settings.yaml", dump_yaml(DEFAULT_SETTINGS))
        if not (root() / "athlete" / "memory.md").exists():
            write_file(root() / "athlete" / "memory.md", MEMORY_TEMPLATE)
        commit_protected("Workspace set up" if not head() else "Guide and skills updated", [".gitignore", "GUIDE.md", ".claude"])
        commit("Workspace defaults", ["settings.yaml", "athlete/memory.md"])
        if not protected_head_path().exists():
            write_file(protected_head_path(), head())
