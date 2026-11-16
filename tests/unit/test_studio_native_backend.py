"""Native Studio host keeps its backend private and cleans up on parent EOF."""

import io
import json
import os
import selectors
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from camoufox_pm import studio_backend
from camoufox_pm.config import get_settings
from camoufox_pm.desktop import AlreadyRunningError, _instance_lock

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def settings_reset():
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("message", [None, "shutdown\n"])
def test_real_backend_parent_eof_and_shutdown_release_shared_lock(tmp_path, message):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CPM_")}
    environment["PYTHONPATH"] = str(ROOT / "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "camoufox_pm.studio_backend", "--data-dir", str(tmp_path)],
        cwd=tmp_path, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=20), "Private backend startup timed out"
        record = json.loads(process.stdout.readline(4097))
        assert record["protocol"] == 1 and record["url"].startswith("http://127.0.0.1:")
        assert record["title"] == "Camoufox Studio · Preview · Separate data"
        with pytest.raises(AlreadyRunningError):
            with _instance_lock(tmp_path):
                pytest.fail("Backend released its live-state lock")
        if message:
            process.stdin.write(message)
            process.stdin.flush()
        process.stdin.close()
        process.stdin = None
        output, _ = process.communicate(timeout=25)
        assert process.returncode == 0 and not output.strip()
        with _instance_lock(tmp_path):
            pass
        with sqlite3.connect(tmp_path / "profiles.db") as db:
            assert db.execute("SELECT count(*) FROM profiles").fetchone()[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_startup_failure_only_returns_fixed_protocol_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr(studio_backend, "serve", Mock(side_effect=ValueError("PRIVATE-FAILURE-CONTENT")))
    output = io.StringIO()
    assert studio_backend.main([], protocol=output, control=io.StringIO()) == 1
    assert json.loads(output.getvalue()) == {"protocol": 1, "error": "backend_start_failed"}
    assert "PRIVATE" not in output.getvalue()


def test_competing_owner_has_fixed_close_other_app_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr(studio_backend, "serve", Mock(side_effect=AlreadyRunningError("PRIVATE-OWNER-DETAILS")))
    output = io.StringIO()
    assert studio_backend.main([], protocol=output, control=io.StringIO()) == 1
    assert json.loads(output.getvalue()) == {"protocol": 1, "error": "backend_already_running"}


def test_failed_shutdown_aborts_before_shared_lock_releases(tmp_path, monkeypatch):
    from camoufox_pm import desktop, main, studio

    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "profiles.db"))
    get_settings.cache_clear()
    monkeypatch.setattr(studio, "check_orphan_browsers", Mock())
    monkeypatch.setattr(main, "app", Mock())
    monkeypatch.setattr(desktop, "_wait_until_serving", Mock(return_value=True))
    monkeypatch.setattr(desktop, "_stop_server", Mock(return_value=False))
    server = Mock()
    monkeypatch.setattr(studio_backend.uvicorn, "Server", Mock(return_value=server))

    class ProcessAbort(BaseException):
        pass

    def abort(status):
        assert status == 1
        with pytest.raises(AlreadyRunningError):
            with _instance_lock(tmp_path):
                pytest.fail("Lock released before backend process abort")
        raise ProcessAbort

    with pytest.raises(ProcessAbort):
        studio_backend.serve(
            tmp_path, tmp_path / "profiles.db", 0, io.StringIO(), io.StringIO(), abort=abort,
        )
