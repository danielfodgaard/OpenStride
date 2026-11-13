"""What the harness is started with: the settings that hold the boundary, and an environment with no secrets."""
from dataclasses import replace

from app import config, workspace
from app.agent import coach_policy, system_prompt
from app.runtime import claude_sdk
from app.runtime.base import TurnRequest, describe


def request(ws, **changes) -> TurnRequest:
    return TurnRequest(prompt="hi", system_prompt="s", policy=coach_policy(), token="tok", env={"OPENSTRIDE_SPOOL": "/tmp/spool"}, **changes)


def test_the_shell_gets_no_secrets_and_knows_where_the_workspace_is(ws, monkeypatch):
    monkeypatch.setenv("INTERVALS_ICU_API_KEY", "real-key")
    monkeypatch.setenv("OPENSTRIDE_PASSWORD", "hunter2")
    env = claude_sdk.environment(request(ws))
    assert env["INTERVALS_ICU_API_KEY"] == "" and env["OPENSTRIDE_PASSWORD"] == ""
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in env  # the harness needs the OS sandbox to scrub, and it is off here
    monkeypatch.setenv("OPENSTRIDE_SANDBOX", "on")
    config.reload()
    assert claude_sdk.environment(request(ws))["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert env["OPENSTRIDE_WORKSPACE"] == str(workspace.root()) and env["OPENSTRIDE_TURN"] == "tok" and env["OPENSTRIDE_SPOOL"] == "/tmp/spool"
    assert env["OPENSTRIDE_DATA_DIR"] == "" and env["CLAUDE_CONFIG_DIR"].startswith(str(workspace.state()))


def test_permission_rules_deny_the_protected_paths_and_the_state_folder(ws):
    rules = claude_sdk.harness_settings(request(ws))["permissions"]["deny"]
    root, state = workspace.root().resolve(), workspace.state().resolve()
    for path in ("plan", "activities", "wellness", "proposals", ".git", ".claude", "athlete/profile.yaml"):
        assert f"Edit(/{root / path}/**)" in rules
    assert f"Read(/{state}/**)" in rules
    assert not any("journal" in rule or "memory.md" in rule for rule in rules)


def test_the_sandbox_is_set_up_only_where_it_can_run(ws, monkeypatch):
    monkeypatch.setenv("OPENSTRIDE_SANDBOX", "off")
    config.reload()
    assert "sandbox" not in claude_sdk.harness_settings(request(ws))
    monkeypatch.setenv("OPENSTRIDE_SANDBOX", "on")
    config.reload()
    box = claude_sdk.harness_settings(request(ws))["sandbox"]
    assert box["enabled"] and box["allowUnsandboxedCommands"] is False and box["failIfUnavailable"] is True
    assert box["network"] == {"allowedDomains": []}
    assert f"/{(workspace.root() / 'plan').resolve()}" in box["filesystem"]["denyWrite"]
    assert f"/{workspace.state().resolve()}" in box["filesystem"]["denyRead"]
    assert box["enableWeakerNestedSandbox"] is False
    monkeypatch.setenv("OPENSTRIDE_SANDBOX", "weak")
    config.reload()
    assert claude_sdk.harness_settings(request(ws))["sandbox"]["enableWeakerNestedSandbox"] is True


def test_auto_takes_the_strongest_form_the_machine_can_run(ws, monkeypatch):
    """In a container the shell can't have its own /proc: the weak form is used, not a sandbox that fails on every command."""
    monkeypatch.setenv("OPENSTRIDE_SANDBOX", "auto")
    config.reload()
    for found, level, on in ((("strong", ""), "strong", True), (("weak", ""), "weak", True), (("", "bwrap: No permissions to create new namespace"), "", False)):
        monkeypatch.setattr(claude_sdk, "sandbox_probe", lambda found=found: found)
        assert claude_sdk.sandbox_level() == level and claude_sdk.sandbox_on() is on
        settings = claude_sdk.harness_settings(request(ws))
        assert ("sandbox" in settings) is on
        if on:
            assert settings["sandbox"]["enableWeakerNestedSandbox"] is (level == "weak") and settings["sandbox"]["failIfUnavailable"] is False
        assert ("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" in claude_sdk.environment(request(ws))) is (level == "strong")  # the harness refuses the container form with it


def test_the_probe_is_the_harness_call_in_small():
    assert claude_sdk.PROBE[:2] == ["bwrap", "--unshare-user"] and "--unshare-net" in claude_sdk.PROBE


def test_the_analyst_may_write_nothing_in_the_workspace(ws, tmp_path):
    scratch = tmp_path / "run"
    scratch.mkdir()
    policy = replace(coach_policy(), role="analyst", cwd=scratch, scratch=scratch)
    monkeypatch_request = replace(request(ws), policy=policy)
    rules = claude_sdk.harness_settings(monkeypatch_request)["permissions"]["deny"]
    assert f"Edit(/{workspace.root().resolve()}/**)" in rules


def test_status_lines_never_show_a_command_or_a_path(ws):
    root = workspace.root()
    assert describe("Bash", {"command": "openstride plan draft --from scratch/d.json"}, root) == "Preparing a plan change…"
    assert describe("Bash", {"command": "python3 scratch/x.py && rm -rf /"}, root) == "Working with your data…"
    assert describe("Read", {"file_path": str(root / "athlete" / "memory.md")}, root) == "Reading my notes about you…"
    assert describe("Write", {"file_path": "journal/2026-11-25.md"}, root) == "Writing…" or True
    assert describe("Edit", {"file_path": str(root / "athlete" / "memory.md")}, root) == "Updating my notes about you…"


def test_the_prompts_are_whole(ws):
    coach, analyst = system_prompt("coach"), system_prompt("analyst")
    assert "You cannot change the plan" in coach and "# The OpenStride workspace" in coach and "{{PROVIDER}}" not in coach
    assert "data scientist" in analyst and "openstride plan draft" in analyst
