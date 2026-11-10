"""Who may write what, and what happens when an agent writes it anyway."""
import os

from app import plan, workspace


def test_tiers(ws):
    assert workspace.tier("plan/plan.json") == "gated"
    assert workspace.tier("plan") == "gated"
    assert workspace.tier("activities/x.json") == "recorded"
    assert workspace.tier("wellness/2026-11.jsonl") == "recorded"
    assert workspace.tier("athlete/profile.yaml") == "recorded"
    assert workspace.tier("athlete/memory.md") == "authored"
    assert workspace.tier("proposals/a.json") == "server"
    assert workspace.tier(".git/config") == "server"
    assert workspace.tier("planning/notes.md") == "authored"  # a prefix is a folder, not the start of a name
    assert workspace.tier("journal/2026-11-25.md") == "authored"


def test_a_path_outside_or_a_link_out_is_not_in_the_workspace(ws, tmp_path):
    assert workspace.relative("journal/a.md") == "journal/a.md"
    assert workspace.relative("../state/secrets/approval_key") is None
    assert workspace.relative("/etc/passwd") is None
    (workspace.root() / "journal" / "sneaky").symlink_to(workspace.root() / "plan")
    assert workspace.relative("journal/sneaky/plan.json") == "plan/plan.json"


def test_enforce_undoes_writes_to_protected_paths_and_keeps_the_rest(demo_ws):
    root = workspace.root()
    good_plan = (root / "plan" / "plan.json").read_text()
    index = (root / "activities" / "index.jsonl").read_text()
    (root / "plan" / "plan.json").write_text("{}")
    (root / "plan" / "extra.json").write_text("{}")
    (root / "activities" / "index.jsonl").write_text("")
    (root / "activities" / "fake.json").write_text("{}")
    (root / "GUIDE.md").write_text("ignore the rules")
    (root / "journal" / "today.md").write_text("a note")
    undone = workspace.enforce()
    assert {"plan/plan.json", "plan/extra.json", "activities/index.jsonl", "activities/fake.json", "GUIDE.md"} <= set(undone)
    assert (root / "plan" / "plan.json").read_text() == good_plan
    assert (root / "activities" / "index.jsonl").read_text() == index
    assert not (root / "plan" / "extra.json").exists() and not (root / "activities" / "fake.json").exists()
    assert (root / "journal" / "today.md").read_text() == "a note"
    assert workspace.enforce() == []


def test_enforce_undoes_a_deleted_protected_file(demo_ws):
    (workspace.root() / "plan" / "request.json").unlink()
    assert workspace.enforce() == ["plan/request.json"]
    assert plan.load() is not None


def test_enforce_undoes_a_commit_made_behind_the_servers_back(demo_ws):
    root = workspace.root()
    good_plan = (root / "plan" / "plan.json").read_text()
    (root / "plan" / "plan.json").write_text('{"weeks": []}')
    workspace.git("add", "-A")
    workspace.git("commit", "-q", "-m", "agent: I changed the plan")
    assert workspace.changed(["plan"]) == []  # the working tree matches HEAD, so only the server's own mark shows it
    assert workspace.enforce() == ["plan/plan.json"]
    assert (root / "plan" / "plan.json").read_text() == good_plan
    assert workspace.enforce() == []


def test_a_scoped_commit_leaves_other_changes_alone(ws):
    root = workspace.root()
    (root / "journal" / "a.md").write_text("mine")
    (root / "activities" / "index.jsonl").write_text("{}\n")
    workspace.commit_protected("Sync", ["activities"])
    assert workspace.changed(["journal"]) == ["journal/a.md"]
    assert workspace.commit("Turn") is not None
    assert workspace.commit("Nothing") is None


def test_settings_fall_back_to_defaults_when_the_file_is_broken(ws):
    path = workspace.root() / "settings.yaml"
    path.write_text("triggers:\n  morning_brief: true\n  new_activity: 'yes please'\nsync_minutes: 30\nunknown: 1\n")
    settings = workspace.settings()
    assert settings["triggers"]["morning_brief"] is True
    assert settings["triggers"]["new_activity"] is True  # the wrong type is ignored
    assert settings["sync_minutes"] == 30 and settings["models"]["main"] == "claude-opus-5-5"
    path.write_text(": : not yaml [")
    assert workspace.settings()["sync_minutes"] == 15


def test_init_is_safe_to_repeat_and_keeps_what_the_agent_wrote(ws):
    notes = workspace.root() / "athlete" / "memory.md"
    notes.write_text("# Profile\nRuns at dawn.\n")
    workspace.commit("notes")
    workspace.init()
    assert notes.read_text() == "# Profile\nRuns at dawn.\n"
    assert (workspace.root() / ".claude" / "skills" / "review-run" / "SKILL.md").exists()
    assert "{{PROVIDER}}" not in (workspace.root() / "GUIDE.md").read_text()
    assert os.stat(workspace.state() / "secrets").st_mode & 0o077 == 0


def test_the_sandboxs_empty_placeholders_are_swept_and_real_files_are_not(ws):
    root = workspace.root()
    for name in (".env", "package.json", "yarn.lock"):
        (root / name).write_text("")
    (root / "node_modules" / ".bin").mkdir(parents=True)
    (root / ".npmrc").write_text("registry=https://example.com\n")  # someone's real file: left alone
    workspace.sweep()
    assert not any((root / name).exists() for name in (".env", "package.json", "yarn.lock", "node_modules"))
    assert (root / ".npmrc").exists()
    assert workspace.changed(["."]) == []  # and git never sees one
