"""Shared-state startup must refuse competing owners before touching profile data."""

import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from camoufox_pm import studio
from camoufox_pm.config import get_settings
from camoufox_pm.desktop import AlreadyRunningError, _instance_lock


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    monkeypatch.delenv("CPM_DB_PATH", raising=False)
    monkeypatch.setattr(studio, "check_orphan_browsers", Mock())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def registry(directory: Path, rows=(), owner=None):
    path = directory / "update-control" / "maintenance.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE managers (pid INTEGER, created REAL);"
            "CREATE TABLE gate (id INTEGER, owner TEXT);"
        )
        db.execute("INSERT INTO gate VALUES (1, ?)", (owner,))
        db.executemany("INSERT INTO managers VALUES (?, ?)", rows)
    return path


def test_shared_lock_refuses_before_bootstrap(tmp_path, monkeypatch):
    bootstrap = Mock()
    monkeypatch.setattr(studio, "bootstrap_desktop", bootstrap)
    with _instance_lock(tmp_path):
        assert studio.main(["--data-dir", str(tmp_path), "--check"]) == 1
    bootstrap.assert_not_called()
    assert not (tmp_path / "profiles.db").exists()
    assert not (tmp_path / "secret.key").exists()


def test_fork_lock_blocks_original_desktop_and_releases(tmp_path):
    with studio.shared_state(tmp_path):
        with pytest.raises(AlreadyRunningError):
            with _instance_lock(tmp_path):
                pytest.fail("original desktop was admitted")
    with _instance_lock(tmp_path):
        pass


def test_active_legacy_manager_is_rejected_without_registry_writes(tmp_path, monkeypatch):
    path = registry(tmp_path, [(9876, 12.5)])
    before = path.read_bytes()
    monkeypatch.setattr(studio.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 12.5))
    with pytest.raises(AlreadyRunningError, match="Close Camoufox"):
        studio.check_registered_managers(tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("exists", [False, True])
def test_stale_and_reused_pid_are_ignored(tmp_path, monkeypatch, exists):
    registry(tmp_path, [(9876, 12.5)])

    def process(pid):
        if not exists:
            raise psutil.NoSuchProcess(pid)
        return SimpleNamespace(create_time=lambda: 99.0)

    monkeypatch.setattr(studio.psutil, "Process", process)
    studio.check_registered_managers(tmp_path)


def test_process_access_denied_blocks_startup(tmp_path, monkeypatch):
    registry(tmp_path, [(9876, 12.5)])
    monkeypatch.setattr(studio.psutil, "Process", Mock(side_effect=psutil.AccessDenied(9876)))
    with pytest.raises(AlreadyRunningError):
        studio.check_registered_managers(tmp_path)


def test_recovery_owner_blocks_startup(tmp_path):
    registry(tmp_path, owner="pending-test-recovery")
    with pytest.raises(RuntimeError, match="update recovery"):
        studio.check_registered_managers(tmp_path)


def test_corrupt_registry_is_not_recreated(tmp_path):
    path = tmp_path / "update-control" / "maintenance.sqlite3"
    path.parent.mkdir()
    path.write_bytes(b"not a database")
    with pytest.raises(RuntimeError, match="cannot be checked"):
        studio.check_registered_managers(tmp_path)
    assert path.read_bytes() == b"not a database"


def test_existing_database_without_key_does_not_get_replacement(tmp_path, monkeypatch):
    database = tmp_path / "profiles.db"
    database.write_bytes(b"existing state")
    bootstrap = Mock()
    monkeypatch.setattr(studio, "bootstrap_desktop", bootstrap)
    assert studio.main(["--data-dir", str(tmp_path), "--no-dialog"]) == 1
    bootstrap.assert_not_called()
    assert database.read_bytes() == b"existing state"
    assert not (tmp_path / "secret.key").exists()


def test_bootstrap_and_server_run_only_while_lock_is_held(tmp_path, monkeypatch):
    calls = []

    def assert_owned():
        with pytest.raises(AlreadyRunningError):
            with _instance_lock(tmp_path):
                pytest.fail("shared lock was absent")

    def bootstrap():
        assert_owned()
        assert get_settings().db_path == str(tmp_path / "profiles.db")
        calls.append("bootstrap")

    def serve(**kwargs):
        assert_owned()
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["title"] == studio.TITLE
        calls.append("serve")

    monkeypatch.setattr(studio, "bootstrap_desktop", bootstrap)
    monkeypatch.setattr(studio, "_run_desktop_locked", serve)
    assert studio.main(["--data-dir", str(tmp_path), "--no-dialog"]) == 0
    assert calls == ["bootstrap", "serve"]
    with _instance_lock(tmp_path):
        pass


def test_uncertain_shutdown_keeps_lock_until_process_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "bootstrap_desktop", Mock())
    monkeypatch.setattr(
        studio, "_run_desktop_locked", Mock(side_effect=studio.DesktopShutdownError("shutdown failed"))
    )

    def exit_process(code):
        assert code == 1
        with pytest.raises(AlreadyRunningError):
            with _instance_lock(tmp_path):
                pytest.fail("ownership released before process exit")
        raise SystemExit(code)

    monkeypatch.setattr(studio.os, "_exit", exit_process)
    with pytest.raises(SystemExit):
        studio.main(["--data-dir", str(tmp_path), "--no-dialog"])


def test_separate_test_directory_is_independent(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    with studio.shared_state(first), studio.shared_state(second):
        pass


def test_crashed_lock_owner_is_released_by_os(tmp_path):
    code = (
        "from pathlib import Path; from camoufox_pm.desktop import _instance_lock; "
        "import sys,time; "
        "guard=_instance_lock(Path(sys.argv[1])); guard.__enter__(); "
        "print('ready',flush=True); time.sleep(30)"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(AlreadyRunningError):
            with studio.shared_state(tmp_path):
                pytest.fail("competing owner was admitted")
    finally:
        child.kill()
        child.wait(timeout=5)
    with studio.shared_state(tmp_path):
        pass


def test_orphan_browser_uses_only_shared_profile_root(tmp_path, monkeypatch):
    monkeypatch.undo()
    browser = SimpleNamespace(
        info={"name": "camoufox"},
        cmdline=lambda: ["camoufox", "-profile", str(tmp_path / "profiles" / "example")],
    )
    monkeypatch.setattr(studio.psutil, "process_iter", lambda fields: [browser])
    with pytest.raises(AlreadyRunningError, match="browser still uses"):
        studio.check_orphan_browsers(tmp_path)
    studio.check_orphan_browsers(tmp_path / "separate")


def test_default_directory_matches_installed_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "desktop_data_dir", lambda: tmp_path)
    assert studio.data_directory() == tmp_path


def test_explicit_directory_and_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "custom" / "profiles.db"))
    assert studio.data_directory() == tmp_path / "custom"
    assert studio.data_directory(str(tmp_path / "test")) == tmp_path / "test"


def test_nonstandard_database_name_is_preserved(monkeypatch, tmp_path):
    expected = tmp_path / "custom.db"
    monkeypatch.setenv("CPM_DB_PATH", str(expected))
    assert studio.database_path() == expected
    assert studio.main(["--check"]) == 0
    assert get_settings().db_path == str(expected)
    assert not (tmp_path / "profiles.db").exists()


@pytest.mark.parametrize("flag", ["-profile=", "--profile=", "-PROFILE="])
def test_equals_profile_flag_is_detected(tmp_path, monkeypatch, flag):
    monkeypatch.undo()
    browser = SimpleNamespace(
        info={"name": "camoufox"},
        cmdline=lambda: ["camoufox", flag + str(tmp_path / "profiles" / "example")],
    )
    monkeypatch.setattr(studio.psutil, "process_iter", lambda fields: [browser])
    with pytest.raises(AlreadyRunningError, match="browser still uses"):
        studio.check_orphan_browsers(tmp_path)
