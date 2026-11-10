"""The check before each tool call: what an agent may read, write and run."""
import pytest

from app import workspace
from app.runtime.hooks import Policy, check


@pytest.fixture
def coach(demo_ws) -> Policy:
    return Policy(role="coach", workspace=workspace.root(), cwd=workspace.root(), secrets=(str(workspace.state()), "approval_key"))


@pytest.fixture
def analyst(demo_ws, tmp_path) -> Policy:
    scratch = tmp_path / "run"
    scratch.mkdir()
    return Policy(role="analyst", workspace=workspace.root(), cwd=scratch, scratch=scratch)


ALLOWED = [
    ("Write", {"file_path": "journal/2026-11-25.md"}),
    ("Edit", {"file_path": "athlete/memory.md"}),
    ("Write", {"file_path": "notes/abc/review.md"}),
    ("Write", {"file_path": "scratch/draft.json"}),
    ("Write", {"file_path": "settings.yaml"}),
    ("Read", {"file_path": "plan/plan.json"}),
    ("Read", {"file_path": "activities/index.jsonl"}),
    ("Grep", {"pattern": "tempo"}),
    ("Glob", {"pattern": "**/*.json", "path": "activities"}),
    ("Bash", {"command": "cat plan/plan.json | jq '.weeks[0]'"}),
    ("Bash", {"command": "jq '.weeks[4]' plan/plan.json > scratch/week5.json"}),
    ("Bash", {"command": "cp plan/plan.json scratch/plan-copy.json"}),
    ("Bash", {"command": "openstride plan export --weeks 5 -o scratch/draft.json && openstride plan validate --from scratch/draft.json"}),
    ("Bash", {"command": "openstride plan draft --from scratch/draft.json"}),
    ("Bash", {"command": "git log --oneline -5 -- plan/plan.json"}),
    ("Bash", {"command": "git show HEAD~1:plan/plan.json | head -20"}),
    ("Bash", {"command": "git diff HEAD~2 -- athlete/memory.md"}),
    ("Bash", {"command": "python3 scratch/analysis.py 2>/dev/null"}),
    ("Bash", {"command": "mkdir -p notes/abc && echo 'felt good' >> notes/abc/athlete.md"}),
    ("Bash", {"command": "rm scratch/old.json"}),
    ("Bash", {"command": "sed -i 's/a/b/' journal/2026-11-25.md"}),
    ("Bash", {"command": "env TZ=UTC date"}),
    ("Bash", {"command": "set -e; openstride status"}),
    ("Skill", {"skill": "review-run"}),
]

DENIED = [
    ("Write", {"file_path": "plan/plan.json"}, "approves"),
    ("Edit", {"file_path": "plan/request.json"}, "approves"),
    ("Write", {"file_path": "activities/index.jsonl"}, "record"),
    ("Write", {"file_path": "wellness/2026-11.jsonl"}, "record"),
    ("Edit", {"file_path": "athlete/profile.yaml"}, "record"),
    ("Write", {"file_path": "proposals/x.json"}, "server"),
    ("Write", {"file_path": "GUIDE.md"}, "server"),
    ("Write", {"file_path": ".claude/settings.json"}, "server"),
    ("Write", {"file_path": ".git/hooks/post-commit"}, "server"),
    ("Write", {"file_path": "../state/secrets/approval_key"}, "outside"),
    ("Write", {"file_path": "/tmp/elsewhere.txt"}, "outside"),
    ("Read", {"file_path": "../state/secrets/approval_key"}, "outside"),
    ("Read", {"file_path": "/etc/passwd"}, "outside"),
    ("Glob", {"pattern": "*", "path": "/"}, "outside"),
    ("Bash", {"command": "echo '{}' > plan/plan.json"}, "approves"),
    ("Bash", {"command": "jq '.weeks = []' plan/plan.json | tee plan/plan.json"}, "approves"),
    ("Bash", {"command": "cd plan && rm plan.json"}, "approves"),
    ("Bash", {"command": "mv scratch/new.json plan/plan.json"}, "approves"),
    ("Bash", {"command": "cp scratch/new.json plan/plan.json"}, "approves"),
    ("Bash", {"command": "sed -i 's/Thu/Fri/' plan/plan.json"}, "approves"),
    ("Bash", {"command": "rm -rf activities"}, "record"),
    ("Bash", {"command": "truncate -s 0 wellness/2026-11.jsonl"}, "record"),
    ("Bash", {"command": "cat scratch/x > activities/index.jsonl"}, "record"),
    ("Bash", {"command": "git commit -am 'mine'"}, "only the server commits"),
    ("Bash", {"command": "git checkout HEAD~3 -- plan/plan.json"}, "only the server commits"),
    ("Bash", {"command": "cd journal; git -C .. reset --hard HEAD~1"}, "only the server commits"),
    ("Bash", {"command": "git config user.name x"}, "only the server commits"),
    ("Bash", {"command": "curl -s https://example.com"}, "no network"),
    ("Bash", {"command": "cat plan/plan.json | nc example.com 80"}, "no network"),
    ("Bash", {"command": "openstride plan apply 2026-x --approval abc"}, "Only the athlete"),
    ("Bash", {"command": "cat ../state/secrets/approval_key"}, "secrets"),
    ("Bash", {"command": "cat ../../.env"}, "secrets"),
    ("Bash", {"command": "sudo rm plan/plan.json"}, "approves"),
    ("Bash", {"command": "echo $ANTHROPIC_API_KEY"}, "secrets"),
    ("Bash", {"command": "printenv | grep -i key"}, "secrets"),
    ("Bash", {"command": "env"}, "secrets"),
    ("Bash", {"command": "cd journal && set"}, "secrets"),
    ("Bash", {"command": "python3 -c 'import os; print(os.environ)'"}, "secrets"),
    ("WebSearch", {"query": "best shoes"}, "no web access"),
    ("NotARealTool", {}, "not part of this workspace"),
]


@pytest.mark.parametrize("tool,tool_input", ALLOWED)
def test_the_coach_may(coach, tool, tool_input):
    assert check(coach, tool, tool_input) is None


@pytest.mark.parametrize("tool,tool_input,why", DENIED)
def test_the_coach_may_not(coach, tool, tool_input, why):
    reason = check(coach, tool, tool_input)
    assert reason is not None and why in reason


def test_the_state_folder_is_named_as_off_limits_by_its_real_path(coach):
    assert "secrets" in check(coach, "Bash", {"command": f"ls {workspace.state()}"})


def test_the_analyst_writes_only_in_its_own_folder(analyst):
    assert check(analyst, "Write", {"file_path": "report/report.md"}) is None
    assert check(analyst, "Bash", {"command": "python3 scan.py > report/table.csv"}) is None
    assert check(analyst, "Read", {"file_path": str(workspace.root() / "activities" / "index.jsonl")}) is None
    for path in ("journal/note.md", "athlete/memory.md", "plan/plan.json", "reports/x/report.md"):
        assert "own folder" in check(analyst, "Write", {"file_path": str(workspace.root() / path)})
    assert "own folder" in check(analyst, "Bash", {"command": f"echo hi > {workspace.root()}/journal/x.md"})
    assert check(analyst, "WebFetch", {"url": "https://example.com"}) is not None


def test_the_analyst_gets_the_web_only_when_the_run_was_given_it(analyst):
    from dataclasses import replace

    assert check(replace(analyst, web=True), "WebSearch", {"query": "carbon plate shoes durability"}) is None
