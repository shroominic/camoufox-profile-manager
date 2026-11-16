"""Private backend for the native Studio host.

Only one bounded startup record goes to stdout. Closing the parent's private
stdin pipe requests normal API/browser cleanup before the shared lock releases.
This entry point never starts a second native application or imports pywebview.
"""

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn


def serve(directory: Path, database: Path, port: int, protocol, control, *, abort=None) -> int:
    from camoufox_pm.config import bootstrap_desktop, get_settings
    from camoufox_pm.desktop import _stop_server, _wait_until_serving
    from camoufox_pm.studio import SEPARATE_DATA_TITLE, TITLE, shared_state

    with shared_state(directory, database):
        bootstrap_desktop()
        log_directory = directory / "studio-control"
        log_directory.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(log_directory / "studio.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", buffering=1) as log:
            old_streams = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = log
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind(("127.0.0.1", port))
                    listener.listen(128)
                    actual_port = listener.getsockname()[1]
                    os.environ["CPM_HOST"] = "127.0.0.1"
                    os.environ["CPM_PORT"] = str(actual_port)
                    get_settings.cache_clear()
                    from camoufox_pm.main import app

                    server = uvicorn.Server(uvicorn.Config(
                        app, host="127.0.0.1", port=actual_port, log_level="info",
                    ))
                    thread = threading.Thread(
                        target=server.run, kwargs={"sockets": [listener]}, daemon=True,
                    )
                    thread.start()
                    update_requested = False
                    try:
                        if not _wait_until_serving(server, thread):
                            raise RuntimeError("Studio backend startup failed")
                        title = SEPARATE_DATA_TITLE if os.environ.get("CPM_STUDIO_PREVIEW") == "1" else TITLE
                        protocol.write(json.dumps({
                            "protocol": 1, "url": f"http://127.0.0.1:{actual_port}/", "title": title,
                        }) + "\n")
                        protocol.flush()
                        # EOF also means the native host exited. Keep watching
                        # the API thread too, so it cannot die behind a live UI.
                        requested = threading.Event()

                        def read_control():
                            try:
                                control.readline(1025)
                            finally:
                                requested.set()

                        threading.Thread(target=read_control, daemon=True).start()
                        from camoufox_pm.core.update_runtime import _record

                        while thread.is_alive() and not requested.wait(.2):
                            if (_record() or {}).get("action"):
                                update_requested = True
                                break
                        if not requested.is_set() and not update_requested:
                            raise RuntimeError("Studio backend stopped unexpectedly")
                    finally:
                        if not _stop_server(server, thread):
                            # A live API thread must not survive release of the
                            # shared-state guard. End the entire backend while
                            # still inside it, as the existing desktop does.
                            log.write("Studio backend cleanup did not finish.\n")
                            log.flush()
                            (abort or os._exit)(1)
                            raise RuntimeError("Backend abort unexpectedly returned")
                    if update_requested:
                        from camoufox_pm.core.update_runtime import spawn_helper

                        spawn_helper()
            finally:
                sys.stdout, sys.stderr = old_streams
    return 0


def main(argv=None, *, protocol=None, control=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between zero and 65535")
    protocol = protocol or sys.stdout
    control = control or sys.stdin
    ownership_errors = ()
    try:
        from camoufox_pm.config import get_settings
        from camoufox_pm.desktop import AlreadyRunningError
        from camoufox_pm.studio import database_path

        ownership_errors = (AlreadyRunningError,)
        database = database_path(str(args.data_dir) if args.data_dir else None)
        os.environ["CPM_DB_PATH"] = str(database)
        if args.data_dir:
            os.environ["CPM_STUDIO_PREVIEW"] = "1"
        else:
            os.environ.pop("CPM_STUDIO_PREVIEW", None)
        get_settings.cache_clear()
        return serve(database.parent, database, args.port, protocol, control)
    except Exception as error:
        # Startup exceptions and private model values never cross this pipe.
        try:
            reason = "backend_already_running" if isinstance(error, ownership_errors) else "backend_start_failed"
            protocol.write(json.dumps({"protocol": 1, "error": reason}) + "\n")
            protocol.flush()
        except (OSError, ValueError):
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
