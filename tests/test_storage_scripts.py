"""scripts/phone-access*.sh against a fake `tailscale`. Real Tailscale is not available in tests."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path("scripts").resolve()
RUNNING = json.dumps({"BackendState": "Running", "CertDomains": ["laptop.tail1234.ts.net"]}, indent=2)
NO_CERT = json.dumps({"BackendState": "Running", "CertDomains": None}, indent=2)
LOGGED_OUT = json.dumps({"BackendState": "NeedsLogin", "CertDomains": None}, indent=2)


def run(script, path, **env):
    return subprocess.run(
        ["/bin/bash", str(SCRIPTS / script)],
        env={"PATH": f"{path}:/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )


def fake_tailscale(folder, status_json, status_exit=0):
    """Records every call in calls.log; `status` prints what the case needs."""
    (folder / "status.json").write_text(status_json)
    shim = folder / "tailscale"
    shim.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{folder}/calls.log"\n'
        f'if [ "$1" = status ]; then cat "{folder}/status.json"; exit {status_exit}; fi\n'
    )
    shim.chmod(0o755)
    return folder


def calls(folder):
    log = folder / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(bool(shutil.which("tailscale", path="/usr/bin:/bin")), reason="real tailscale on the system PATH")
def test_not_installed(tmp_path):
    result = run("phone-access.sh", tmp_path)
    assert result.returncode == 1 and "not installed" in result.stderr


def test_daemon_not_running(tmp_path):
    result = run("phone-access.sh", fake_tailscale(tmp_path, "", status_exit=1))
    assert result.returncode == 1 and "not running" in result.stderr


def test_logged_out(tmp_path):
    folder = fake_tailscale(tmp_path, LOGGED_OUT)
    result = run("phone-access.sh", folder)
    assert result.returncode == 1 and "not logged in" in result.stderr and "NeedsLogin" in result.stderr
    assert calls(folder) == ["status --json"]


def test_no_certificate_prints_the_tailnet_setting(tmp_path):
    folder = fake_tailscale(tmp_path, NO_CERT)
    result = run("phone-access.sh", folder)
    assert result.returncode == 1 and "MagicDNS" in result.stderr and "HTTPS Certificates" in result.stderr
    assert not any(call.startswith("serve") for call in calls(folder))


def test_success_serves_localhost_and_prints_the_url(tmp_path):
    folder = fake_tailscale(tmp_path, RUNNING)
    result = run("phone-access.sh", folder, OPENSTRIDE_PORT="8123")
    assert result.returncode == 0
    assert "https://laptop.tail1234.ts.net" in result.stdout
    assert calls(folder)[-1] == "serve --bg --https=443 http://127.0.0.1:8123"


def test_serve_failure_is_reported(tmp_path):
    folder = fake_tailscale(tmp_path, RUNNING)
    shim = folder / "tailscale"
    shim.write_text(shim.read_text() + 'echo "serve refused" >&2; exit 7\n')
    result = run("phone-access.sh", folder)
    assert result.returncode == 7 and "serve refused" in result.stderr and "https://" not in result.stdout


def test_off_resets_serve(tmp_path):
    folder = fake_tailscale(tmp_path, RUNNING)
    result = run("phone-access-off.sh", folder)
    assert result.returncode == 0 and calls(folder) == ["serve reset"]


def test_compose_tailscale_config_is_valid_json():
    config = json.loads((SCRIPTS / "tailscale" / "serve.json").read_text())
    assert config["AllowFunnel"] == {"${TS_CERT_DOMAIN}:443": False}
