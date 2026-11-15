"""Supervise database-defined Python scripts without persisting their inputs."""

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from camoufox_pm.core.script_models import validate_run_inputs
from camoufox_pm.core.script_store import ScriptConflict, ScriptNotFound, public_run
from camoufox_pm.script_worker import ERROR_CODES, safe_result, secret_tokens

ACTIVE = {"queued", "running", "stopping"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


class ScriptService:
    def __init__(self, store, manager_url, *, import_paths=(), interpreter=None):
        self.store = store
        self.manager_url = manager_url
        self.import_paths = [str(Path(path).expanduser().resolve()) for path in import_paths]
        self.interpreter = interpreter or sys.executable
        self.root = store.path.parent / "studio-control" / "script-runs"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._tasks = {}
        self._processes = {}
        self._stops = set()
        self._admission = asyncio.Lock()
        self._closing = False
        self.store.recover_active_runs()

    def capabilities(self):
        return {
            "language": "python", "available": not self._closing,
            "max_concurrent_runs": 1,
            "active_run_id": next(iter(self._tasks), None),
            "import_roots_configured": len(self.import_paths),
        }

    def _lock(self):
        import fcntl

        path = self.store.path.parent / "studio-control" / "script-run.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise ScriptConflict("Another script is still running. Stop it before starting another.") from None
        return descriptor

    async def start(self, script_id, inputs, resume_of=None, manager_headers=None):
        async with self._admission:
            if self._closing or self._tasks:
                raise ScriptConflict("Another script is running or Studio is closing.")
            definition = self.store.get_script(script_id)
            if not definition or definition.get("archived"):
                raise ScriptNotFound("Script not found.")
            if not definition["enabled"]:
                raise ScriptConflict("This script is disabled.")
            resume = self.store.get_run(resume_of) if resume_of else None
            if resume_of:
                if (
                    not resume or resume["script_id"] != script_id
                    or resume["status"] not in {"failed", "cancelled", "interrupted"}
                    or resume["revision"] != definition["revision"]
                ):
                    raise ScriptConflict("This run cannot be resumed with the current script revision.")
                definition = {**resume["snapshot"], "id": script_id, "revision": resume["revision"]}
            clean_inputs = validate_run_inputs(definition, inputs)
            descriptor = self._lock()
            run = None
            try:
                run = self.store.create_run(definition, resume_of=resume_of)
                run_dir = self.root / run["id"]
                run_dir.mkdir(mode=0o700)
                packet = {
                    "script": run["snapshot"], "inputs": clean_inputs,
                    "manager_url": self.manager_url,
                    "manager_headers": {key: value for key, value in (manager_headers or {}).items() if key in {"Cookie", "X-API-Key"}},
                    "run_dir": str(run_dir),
                    "resume_dir": str(self.root / resume_of) if resume_of else None,
                    "import_paths": self.import_paths,
                    "parent_pid": os.getpid(),
                }
                self.store.append_event(run["id"], "queued")
                task = asyncio.create_task(self._execute(run["id"], packet, descriptor), name="studio-script-" + run["id"])
                self._tasks[run["id"]] = task
                return public_run(self.store.get_run(run["id"]))
            except BaseException:
                try:
                    if run is not None:
                        self.store.update_run(run["id"], status="failed", error_code="worker_unavailable", finished_at=now())
                finally:
                    os.close(descriptor)
                raise

    async def stop(self, run_id):
        run = self.store.get_run(run_id)
        if not run:
            raise ScriptNotFound("Script run not found.")
        if run["status"] in TERMINAL:
            return public_run(run)
        if run_id not in self._tasks:
            raise ScriptConflict("This run is no longer controlled by this Studio process.")
        self._stops.add(run_id)
        self.store.update_run(run_id, status="stopping")
        self.store.append_event(run_id, "stopping")
        # The supervisor sends one interrupt at its next short poll. Sending
        # twice could interrupt the script's credential/input-release cleanup.
        return public_run(self.store.get_run(run_id))

    @staticmethod
    def _signal(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    async def _kill(self, process, *, already_interrupted=False):
        if process.returncode is not None:
            return
        if already_interrupted:
            self._signal(process, signal.SIGKILL)
            await process.wait()
            return
        self._signal(process, signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=8)
        except asyncio.TimeoutError:
            self._signal(process, signal.SIGKILL)
            await process.wait()

    def _environment(self):
        # In particular, exclude CPM_SECRET_KEY, provider keys, proxy logins,
        # shell account variables, and unrelated Python startup hooks.
        names = {"HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME"}
        env = {key: value for key, value in os.environ.items() if key in names}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        env["PYTHONUNBUFFERED"] = "1"
        return env

    async def _execute(self, run_id, packet, descriptor):
        process = None
        terminal = None
        error_code = None
        result = {}
        interrupted = False
        definition = packet["script"]
        private_tokens = secret_tokens(definition, packet["inputs"], packet.get("manager_headers"))
        try:
            if run_id in self._stops:
                terminal, error_code = "cancelled", "stopped"
                return
            process = await asyncio.create_subprocess_exec(
                self.interpreter, "-m", "camoufox_pm.script_worker",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=self._environment(),
                cwd=packet["run_dir"], start_new_session=True,
                pass_fds=(descriptor,), limit=8192,
            )
            self._processes[run_id] = process
            if run_id in self._stops:
                # No source or private input reaches a worker cancelled while
                # its process was being created.
                terminal, error_code = "cancelled", "stopped"
                return
            self.store.update_run(run_id, status="stopping" if run_id in self._stops else "running", started_at=now())
            self.store.append_event(run_id, "started")
            process.stdin.write(json.dumps(packet, allow_nan=False).encode())
            await process.stdin.drain()
            process.stdin.close()
            # Drop the parent copies before executing user code. Private values
            # exist only in the child payload and this short-lived token filter.
            packet.clear()
            deadline = asyncio.get_running_loop().time() + definition["max_runtime_seconds"]
            stopped_at = None
            count = 0
            message = None
            stages = {item["id"] for item in definition["stages"]}
            last_stage = None
            saved_events = 0
            while True:
                if run_id in self._stops:
                    if stopped_at is None:
                        stopped_at = asyncio.get_running_loop().time()
                        self._signal(process, signal.SIGINT)
                        interrupted = True
                    if asyncio.get_running_loop().time() - stopped_at > 8:
                        terminal, error_code = "cancelled", "stopped"
                        break
                if asyncio.get_running_loop().time() > deadline:
                    terminal, error_code = "failed", "timeout"
                    break
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                count += 1
                if len(line) > 4096 or count > 2048:
                    terminal, error_code = "failed", "worker_protocol_error"
                    break
                value = json.loads(line)
                if not isinstance(value, dict) or message is not None:
                    raise ValueError("Invalid worker message")
                if value.get("kind") == "stage" and set(value) == {"kind", "stage"}:
                    if value["stage"] not in stages:
                        raise ValueError("Invalid stage")
                    if value["stage"] != last_stage:
                        saved_events += 1
                        if saved_events > 480:
                            raise ValueError("Worker exceeded the stage event limit")
                        self.store.update_run(run_id, stage=value["stage"])
                        self.store.append_event(run_id, "stage", stage=value["stage"])
                        last_stage = value["stage"]
                elif value.get("kind") == "complete" and set(value) == {"kind", "result"}:
                    result = safe_result(value["result"], private_tokens)
                    message = "complete"
                elif value.get("kind") == "error" and set(value) == {"kind", "error_code"}:
                    error_code = value["error_code"] if value["error_code"] in ERROR_CODES else "script_error"
                    message = "error"
                else:
                    raise ValueError("Invalid worker message")
            if terminal is None:
                try:
                    code = await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    code = None
                if message == "complete" and code == 0:
                    terminal = "succeeded"
                    error_code = None
                elif run_id in self._stops:
                    terminal, error_code = "cancelled", "stopped"
                else:
                    terminal = "failed"
                    error_code = error_code or "worker_protocol_error"
        except asyncio.CancelledError:
            terminal, error_code = "interrupted", "interrupted"
        except OSError:
            terminal, error_code = "failed", "worker_unavailable"
        except Exception:
            terminal, error_code = "failed", "worker_protocol_error"
        finally:
            try:
                if process:
                    await self._kill(process, already_interrupted=interrupted)
                packet.clear()
                private_tokens.clear()
                terminal = terminal or "failed"
                self.store.update_run(run_id, status=terminal, error_code=error_code, result=result if terminal == "succeeded" else {}, finished_at=now())
                self.store.append_event(run_id, terminal)
            finally:
                self._processes.pop(run_id, None)
                self._stops.discard(run_id)
                self._tasks.pop(run_id, None)
                os.close(descriptor)

    async def shutdown(self):
        self._closing = True
        for run_id in list(self._tasks):
            await self.stop(run_id)
        if self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
