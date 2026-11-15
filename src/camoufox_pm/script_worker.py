"""Execute a reviewed database script in a child process with private input.

This is a process boundary, not a Python sandbox. Only an administrator can
author or run code. Ordinary stdout/stderr never become stored run output.
"""

import contextlib
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_PACKET = 4 * 1024 * 1024
ERROR_CODES = {
    "script_error", "stopped", "timeout", "worker_unavailable", "worker_protocol_error",
    "interrupted", "dependency_unavailable", "preflight_failed", "invalid_inputs",
}


class ScriptFailure(Exception):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "script_error"


def secret_tokens(definition, inputs, headers=None):
    values = set()
    for value in (headers or {}).values():
        if isinstance(value, str) and value:
            values.add(value)
            if "=" in value:
                values.add(value.partition("=")[2])
    for field in definition.get("inputs", []):
        if field.get("type") != "secret":
            continue
        value = inputs.get(field["name"])
        if not isinstance(value, str) or not value:
            continue
        for text in {value, urllib.parse.unquote(value)}:
            values.add(text)
            values.update(part for part in re.split(r"[:\s]+", text) if part)
            if "?" in text:
                for item in urllib.parse.parse_qs(text.partition("?")[2]).values():
                    values.update(item)
    return values


def safe_result(value, tokens):
    """Only small, flat metadata is returned; known private values are dropped."""
    if not isinstance(value, dict) or len(value) > 20:
        raise ScriptFailure("worker_protocol_error")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", key):
            raise ScriptFailure("worker_protocol_error")
        if re.search(r"password|secret|credential|token|email|cookie|account_record", key, re.I):
            continue
        if item is not None and type(item) not in (str, bool, int, float):
            raise ScriptFailure("worker_protocol_error")
        if type(item) is float:
            import math

            if not math.isfinite(item):
                raise ScriptFailure("worker_protocol_error")
        if isinstance(item, str) and (
            len(item) > 256 or any(ord(c) < 32 for c in item)
            or "://" in item or item.startswith(("/", "~", "\\"))
        ):
            continue
        rendered = str(item)
        if any((token in rendered if len(token) >= 4 else token == rendered) for token in tokens):
            continue
        result[key] = item
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class ScriptContext:
    def __init__(self, packet, emit):
        self.inputs = packet["inputs"]
        self.manager_url = packet["manager_url"]
        parsed = urllib.parse.urlsplit(self.manager_url)
        if (
            parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or not parsed.port or parsed.path or parsed.query or parsed.fragment
            or parsed.username or parsed.password
        ):
            raise ScriptFailure("worker_protocol_error")
        self.run_dir = Path(packet["run_dir"])
        self.resume_dir = Path(packet["resume_dir"]) if packet.get("resume_dir") else None
        self._headers = packet.get("manager_headers", {})
        if set(self._headers) - {"Cookie", "X-API-Key"}:
            raise ScriptFailure("worker_protocol_error")
        self._emit = emit
        self._stages = {stage["id"] for stage in packet["script"].get("stages", [])}
        self._tokens = secret_tokens(packet["script"], self.inputs, self._headers)
        self._result = {}
        self._event_count = 0

    def stage(self, identifier):
        if identifier not in self._stages or self._event_count >= 2000:
            raise ScriptFailure("worker_protocol_error")
        self._event_count += 1
        self._emit({"kind": "stage", "stage": identifier})

    def set_result(self, value):
        self._result = safe_result(value, self._tokens)

    def fail(self, code="script_error"):
        raise ScriptFailure(code)

    def manager_request(self, path, payload=None, *, timeout=120):
        """Use the current manager's exact origin and this run's transient auth."""
        if (
            not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
            or "://" in path or any(ord(c) < 32 for c in path)
        ):
            raise ScriptFailure("worker_protocol_error")
        headers = {"Accept": "application/json", **self._headers}
        body = None
        if payload is not None:
            body = json.dumps(payload, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.manager_url + "/api/v1" + path, data=body, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=max(0.1, min(float(timeout), 120))) as response:
                return json.load(response)
        except (OSError, ValueError, urllib.error.URLError):
            raise ScriptFailure("preflight_failed") from None


def _watch_parent(expected):
    while True:
        time.sleep(0.5)
        if os.getppid() != expected:
            os.kill(os.getpid(), signal.SIGINT)
            # A dependency may catch KeyboardInterrupt. A departed manager must
            # not leave an input worker or its inherited admission lock alive.
            time.sleep(8)
            if os.getpgrp() == os.getpid():
                os.killpg(os.getpgrp(), signal.SIGKILL)
            else:
                os._exit(1)
            return


def main():
    control = sys.stdout

    def emit(value):
        control.write(json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n")
        control.flush()

    # Suppression starts before importing script code or dependencies. Nothing
    # printed by account parsing, auth libraries, or an exception reaches logs.
    with open(os.devnull, "w") as discarded, contextlib.redirect_stdout(discarded), contextlib.redirect_stderr(discarded):
        try:
            payload = sys.stdin.buffer.read(MAX_PACKET + 1)
            if len(payload) > MAX_PACKET:
                raise ScriptFailure("worker_protocol_error")
            packet = json.loads(payload)
            del payload
            if packet.get("parent_pid") != os.getppid():
                raise ScriptFailure("worker_protocol_error")
            context = ScriptContext(packet, emit)
            for root in reversed(packet.get("import_paths", [])):
                path = Path(root)
                if path.is_dir():
                    sys.path.insert(0, str(path))
            threading.Thread(target=_watch_parent, args=(packet["parent_pid"],), daemon=True).start()
            code = packet["script"]["code"]
            namespace = {"__name__": "__main__", "__file__": "<studio-script>", "context": context}
            exec(compile(code, "<studio-script>", "exec"), namespace)
            emit({"kind": "complete", "result": context._result})
            return 0
        except KeyboardInterrupt:
            emit({"kind": "error", "error_code": "stopped"})
            return 130
        except ScriptFailure as exc:
            emit({"kind": "error", "error_code": exc.code})
            return 1
        except ModuleNotFoundError:
            emit({"kind": "error", "error_code": "dependency_unavailable"})
            return 1
        except BaseException:
            emit({"kind": "error", "error_code": "script_error"})
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
