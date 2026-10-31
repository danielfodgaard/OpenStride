"""B5: backups."""
import io
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cli, lifecycle, main, migrate, storage

DEMO = Path("examples/demo/data")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    monkeypatch.setenv("OPENSTRIDE_TODAY", "2026-11-25")
    shutil.copytree(DEMO, storage.DATA_DIR)
    # What a used folder also holds: locks, a stray temp file, and secrets.
    storage.session_secret()
    with storage.activities_lock():
        pass
    with storage.plan_lock(lifecycle.active_plan().meta.id):
        pass
    (storage.DATA_DIR / "activities" / ".x.json.abc.tmp").write_text("half")
    return TestClient(main.app)


def names(response):
    return zipfile.ZipFile(io.BytesIO(response.content)).namelist()


def test_backup_is_a_named_zip_without_locks_or_secrets(client):
    response = client.get("/api/backup")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert f'filename="openstride-backup-{date.today()}.zip"' in response.headers["content-disposition"]
    files = names(response)
    assert any(name.endswith("plan.json") for name in files)
    assert not [name for name in files if name.endswith((".lock", ".tmp")) or name.startswith("secrets")]


def test_include_secrets_adds_them(client):
    files = names(client.get("/api/backup", params={"include_secrets": 1}))
    assert "secrets/session_secret" in files
    assert not [name for name in files if name.endswith(".lock")]


def test_nothing_is_left_behind_after_the_download(client, monkeypatch, tmp_path):
    spot = tmp_path / "spot"
    spot.mkdir()
    monkeypatch.setattr(main.tempfile, "mkdtemp", lambda: str(spot))
    client.get("/api/backup")
    assert not spot.exists()


def test_backup_restores_to_a_working_data_folder(client, tmp_path, monkeypatch):
    plan_id = lifecycle.active_plan().meta.id
    before = client.get(f"/api/plans/{plan_id}").json()
    workouts = client.get("/api/activities").json()
    proposals = client.get(f"/api/plans/{plan_id}/proposals").json()
    zipped = client.get("/api/backup").content

    restored = tmp_path / "restored"
    zipfile.ZipFile(io.BytesIO(zipped)).extractall(restored)
    monkeypatch.setattr(storage, "DATA_DIR", restored)

    assert client.get("/api/plans/active").json() == before
    assert client.get("/api/activities").json() == workouts
    filed = next(item for item in workouts if item.get("has_series") is not False and not item.get("manual"))
    assert client.get(f"/api/activities/{filed['id']}/series").status_code == 200
    pending = next(p for p in proposals if p["status"] == "pending")
    assert client.post(f"/api/proposals/{pending['proposal_id']}/accept").json()["meta"]["version"] == 2
    assert client.get("/api/health").json()["data_dir_writable"] is True


def test_earlier_backups_in_the_data_folder_are_not_nested(client):
    storage.write_backup(storage.DATA_DIR / "backups")
    assert not [name for name in names(client.get("/api/backup")) if name.endswith(".zip")]


def test_a_file_deleted_during_the_backup_is_skipped(client, monkeypatch, tmp_path):
    real = zipfile.ZipFile.write

    def write(self, path, *args):
        if str(path).endswith("plan.json"):
            raise FileNotFoundError(path)
        return real(self, path, *args)

    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    assert storage.write_backup(tmp_path / "out").exists()


def test_failed_backup_leaves_no_zip_or_temp(client, monkeypatch, tmp_path):
    monkeypatch.setattr(zipfile.ZipFile, "write", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        storage.write_backup(tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_console_script_backup(client, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["openstride", "backup", str(tmp_path / "out")])
    cli.main()
    path = Path(capsys.readouterr().out.strip())
    assert path.parent == tmp_path / "out" and zipfile.is_zipfile(path)
    assert not [n for n in zipfile.ZipFile(path).namelist() if n.startswith("secrets")]


def test_backup_script_with_the_project_environment(tmp_path):
    """The real script through `uv run`, on a data folder of its own."""
    if not shutil.which("uv"):
        pytest.skip("uv not installed")
    data = tmp_path / "data"
    shutil.copytree(DEMO, data)
    script = str(Path("scripts/backup.sh").resolve())
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "OPENSTRIDE_DATA_DIR": str(data)}
    result = subprocess.run(["bash", script, str(tmp_path / "out")], env=env, capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert any(n.endswith("plan.json") for n in zipfile.ZipFile(result.stdout.strip()).namelist())
    usage = subprocess.run(["bash", script], env=env, capture_output=True, text=True)
    assert usage.returncode == 2 and "Usage" in usage.stderr


def test_migration_backs_up_first(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    shutil.copytree(DEMO, storage.DATA_DIR)
    source = tmp_path / "plans"
    source.mkdir()
    shutil.copy("examples/sample_plan.json", source / "old.json")
    order = []
    real_backup, real_save = storage.write_backup, storage.save_plan
    monkeypatch.setattr(storage, "write_backup", lambda f: (order.append("backup"), real_backup(f))[1])
    monkeypatch.setattr(storage, "save_plan", lambda p: (order.append("save"), real_save(p))[1])
    assert len(migrate.migrate(source)) == 1
    assert order == ["backup", "save"]
    assert len(list((storage.DATA_DIR / "backups").glob("*.zip"))) == 1


def test_migration_with_nothing_to_do_writes_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    source = tmp_path / "plans"
    source.mkdir()
    assert migrate.migrate(source) == []
    assert not (tmp_path / "data" / "backups").exists()
