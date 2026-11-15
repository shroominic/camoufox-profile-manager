"""Development desktop entry point sharing the installed manager's data.

The lock must precede bootstrap and any import of main (both can write files).
No browser process, profile lease, or native profile lock is ever taken over.
"""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import psutil

from camoufox_pm.config import Settings, bootstrap_desktop, desktop_data_dir, get_settings
from camoufox_pm.desktop import (
    AlreadyRunningError,
    DesktopShutdownError,
    _instance_lock,
    _run_desktop_locked,
    _show_error,
)

TITLE = "Camoufox Studio · Development"
SEPARATE_DATA_TITLE = "Camoufox Studio · Preview · Separate data"
CLOSE_OTHER = (
    "Close Camoufox Profile Manager or the other Camoufox Studio window, then open "
    "Camoufox Studio again. Both apps use the same profiles and saved browser data. "
    "The running app has not been stopped."
)


def database_path(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve() / "profiles.db"
    settings = Settings()
    if "db_path" in settings.model_fields_set:
        return Path(settings.db_path).expanduser().resolve()
    return desktop_data_dir().resolve() / "profiles.db"


def data_directory(override: str | None = None) -> Path:
    return database_path(override).parent


def check_registered_managers(directory: Path) -> None:
    """Catch an already-running legacy web/CLI manager without modifying its gate."""
    registry = directory / "update-control" / "maintenance.sqlite3"
    if not registry.exists():
        return
    try:
        with sqlite3.connect(f"{registry.as_uri()}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT pid, created FROM managers").fetchall()
            owner = connection.execute("SELECT owner FROM gate WHERE id=1").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(
            "The shared manager status cannot be checked. Open the original manager "
            "and resolve its recovery status before you start Studio."
        ) from exc
    if owner and owner[0] is not None:
        raise RuntimeError(
            "The shared manager data needs update recovery. Complete recovery in the "
            "original app before you start Studio."
        )
    for pid, created in rows:
        if pid == os.getpid():
            continue
        try:
            alive = abs(psutil.Process(pid).create_time() - created) < 0.01
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, TypeError, ValueError) as exc:
            raise AlreadyRunningError(CLOSE_OTHER) from exc
        if alive:
            raise AlreadyRunningError(CLOSE_OTHER)


def check_orphan_browsers(directory: Path) -> None:
    """Reject visible browser processes using profile directories in this data root."""
    for process in psutil.process_iter(["name"]):
        name = (process.info.get("name") or "").lower()
        if "camoufox" not in name and "firefox" not in name:
            continue
        try:
            args = process.cmdline()
            candidates = []
            for index, argument in enumerate(args):
                normalized = argument.lower()
                if normalized in ("-profile", "--profile") and index + 1 < len(args):
                    candidates.append(args[index + 1])
                elif normalized.startswith(("--profile=", "-profile=")):
                    candidates.append(argument.partition("=")[2])
            for candidate in candidates:
                path = Path(candidate).expanduser()
                if not path.is_absolute():
                    path = Path(process.cwd()) / path
                if path.resolve().is_relative_to(directory):
                    raise AlreadyRunningError(
                        "A Camoufox browser still uses the shared profile folder. Close "
                        "that browser normally, then open Studio again. Its state and "
                        "profile lock have not been changed."
                    )
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            raise AlreadyRunningError(
                "Studio cannot check an open browser. Close the Camoufox browsers "
                "and try again. No profile locks have been changed."
            ) from exc


@contextmanager
def shared_state(directory: Path, database: Path | None = None):
    """Hold exactly the same OS lock as the original desktop app."""
    try:
        with _instance_lock(directory):
            check_registered_managers(directory)
            check_orphan_browsers(directory)
            settings = Settings()
            if (database or directory / "profiles.db").exists() and not (
                (directory / "secret.key").is_file() or settings.secret_key
            ):
                raise RuntimeError(
                    "The existing profile database has no encryption key. Restore its "
                    "original secret.key or configure its existing key before starting. "
                    "Studio will not generate a replacement key."
                )
            yield
    except AlreadyRunningError as exc:
        if str(exc).startswith("Camoufox Profile Manager is already open"):
            raise AlreadyRunningError(CLOSE_OTHER) from exc
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the shared-state development fork.")
    parser.add_argument("--data-dir", help="Explicit data folder, mainly for isolated tests")
    parser.add_argument("--check", action="store_true", help="Check ownership; do not start the API")
    parser.add_argument("--no-dialog", action="store_true", help="Print startup errors to stderr")
    parser.add_argument("--port", type=int, default=0, help="Loopback port (default: choose a free port)")
    args = parser.parse_args(argv)
    title = SEPARATE_DATA_TITLE if args.data_dir else TITLE
    database = database_path(args.data_dir)
    directory = database.parent
    # Pin all startup code to this exact data folder. No cwd-relative data copy.
    os.environ["CPM_DB_PATH"] = str(database)
    get_settings.cache_clear()
    try:
        with shared_state(directory, database):
            if args.check:
                print(json.dumps({"available": True, "dataDirectory": str(directory)}))
                return 0
            bootstrap_desktop()
            control = directory / "studio-control"
            control.mkdir(mode=0o700, exist_ok=True)
            log_path = control / "studio.log"
            fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8", buffering=1) as log:
                previous = sys.stdout, sys.stderr
                try:
                    sys.stdout = sys.stderr = log
                    try:
                        _run_desktop_locked(host="127.0.0.1", port=args.port, title=title)
                    except DesktopShutdownError as exc:
                        # A live API thread must never outlive desktop.lock. Keep the
                        # guard during the dialog, then let process exit release it.
                        print(str(exc), file=log)
                        if not args.no_dialog:
                            _show_error(str(exc))
                        log.flush()
                        os._exit(1)
                finally:
                    sys.stdout, sys.stderr = previous
        return 0
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        if not args.no_dialog and not args.check:
            _show_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
