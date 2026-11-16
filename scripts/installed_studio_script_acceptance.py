#!/usr/bin/env python3
"""Exercise the installed Studio worker with public local-fixture credentials.

This creates one test profile and desktop. It never signs into X. A successful
test closes and soft-trashes only its own profile; failed profiles are retained.
Run with the fork's Python and an already running Studio app. --check-only runs
the read-only desktop access check through that same installed worker.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ID = "5701f414-7f4a-4bb9-a414-933494b87284"
PROFILE_ID = re.compile(r"[A-Za-z0-9]{8}")
ACTIVE = {"queued", "running", "stopping"}
STATUSES = ACTIVE | {"succeeded", "failed", "cancelled", "interrupted"}
ERROR_CODES = {
    "script_error", "stopped", "timeout", "worker_unavailable", "worker_protocol_error",
    "interrupted", "dependency_unavailable", "preflight_failed", "invalid_inputs",
    "login_target_changed", "login_field_focus", "login_field_type", "login_page_timeout",
    "login_manual_action", "login_state_unavailable", "missing_proxy_source",
    "ambiguous_proxy_source", "proxy_source_invalid", "desktop_permission_required",
    "desktop_controls_unavailable", "desktop_timeout", "desktop_limit",
    "desktop_selection_uncertain", "desktop_recovery_required",
}
DEFINITION_FIELDS = (
    "name", "description", "language", "code", "inputs", "stages",
    "max_runtime_seconds", "enabled",
)
EXECUTE_LINE = "    result = setup.execute(account, options, **proxy_options)"
PREFLIGHT_SOURCE = '''import create_california_profile as profiles
context.stage("preflight")
if not callable(getattr(profiles, "check_desktop_access", None)):
    context.fail("dependency_unavailable")
profiles.check_desktop_access()
context.set_result({"desktop_access": True})
'''


class AcceptanceError(Exception):
    """Only a fixed harness reason is allowed in the proof."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class API:
    def __init__(self, origin):
        self.origin = origin
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, payload=None, *, method=None, timeout=10):
        headers = {"Accept": "application/json", "X-Studio-Request": "1", "Origin": self.origin}
        key = os.environ.get("CAMOUFOX_PM_API_KEY")
        if key:
            headers["X-API-Key"] = key
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.origin + "/api/v1" + path, data=data, headers=headers, method=method,
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise AcceptanceError(f"api_http_{error.code}") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise AcceptanceError("api_unavailable") from None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def profile_snapshot(api):
    active = []
    for page in range(1, 101):
        response = api.call(f"/profiles?page={page}&per_page=100")
        active.extend(response["profiles"])
        if not response.get("has_next"):
            break
    else:
        raise AcceptanceError("profile_inventory_limit")
    trash = api.call("/trash/profiles")["data"]["profiles"]
    snapshot = {}
    for trashed, records in ((False, active), (True, trash)):
        for record in records:
            identifier = record.get("id")
            if not isinstance(identifier, str) or not PROFILE_ID.fullmatch(identifier):
                raise AcceptanceError("profile_inventory_invalid")
            # Keep only an in-memory digest. Never export names, settings or proxy values.
            projection = {key: record.get(key) for key in (
                "id", "row_version", "name", "updated_at", "deleted_at",
                "browser_settings", "fingerprint",
            )}
            snapshot[identifier] = digest({"trashed": trashed, "record": projection})
    return snapshot, len(active), len(trash)


def desktop_count():
    import create_california_profile as profiles

    snapshot = profiles.read_space_snapshot()
    displays = snapshot.get("displays", [])
    if snapshot.get("available") is not True or not displays:
        return None
    return sum(space.get("type") == 0 for space in displays[0].get("spaces", []))


def lab_definition(record):
    reviewed = json.loads((ROOT / "examples/studio-scripts/signup-script-2.json").read_text())
    if record.get("revision") != 2 or record.get("code") != reviewed["code"]:
        raise AcceptanceError("reviewed_revision_mismatch")
    if record["code"].count(EXECUTE_LINE) != 1:
        raise AcceptanceError("lab_injection_target_invalid")
    definition = {key: record[key] for key in DEFINITION_FIELDS if key in record}
    definition = json.loads(json.dumps(definition))
    definition["name"] = "Installed Studio acceptance LAB " + uuid4().hex[:8]
    definition["description"] = "Public dummy account; local fixture only. Temporary acceptance definition."
    definition["code"] = record["code"].replace(EXECUTE_LINE, "    options.lab = True\n" + EXECUTE_LINE)
    return definition


@contextmanager
def local_fixture():
    import auth_lab_server as fixture

    try:
        server = fixture.ThreadingHTTPServer(("127.0.0.1", 8770), fixture.Handler)
    except OSError:
        raise AcceptanceError("fixture_port_busy") from None
    server.daemon_threads = True
    with fixture.LOCK:
        fixture.EVENTS.clear()
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.1), daemon=True)
    thread.start()
    try:
        yield fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def wait_run(api, identifier, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        run = api.call("/studio/script-runs/" + identifier)
        if run.get("status") not in ACTIVE:
            return run
        time.sleep(.25)
    raise AcceptanceError("acceptance_timeout")


def safe_receipt(data_dir, run_id):
    if not re.fullmatch(r"[a-fA-F0-9-]{36}", run_id):
        raise AcceptanceError("run_identity_invalid")
    path = data_dir / "studio-control/script-runs" / run_id / "setup-receipt.json"
    if not path.is_file():
        return {}
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise AcceptanceError("receipt_invalid")
    value = json.loads(path.read_text())
    result = {}
    for key in ("authenticated", "totpUsed", "placementVerified", "spaceMetadataAvailable", "lab",
                "needsAttention", "recorderRestored"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key in ("profileId", "proxySourceLibrary"):
        if isinstance(value.get(key), str) and PROFILE_ID.fullmatch(value[key]):
            result[key] = value[key]
    for key in ("selectedSpaceId", "expectedSpaceId", "geckoPort", "loginAttempts"):
        if type(value.get(key)) is int and 0 <= value[key] < 2**64:
            result[key] = value[key]
    if value.get("phase") in {
        "prepared", "create_requested", "profile_created", "desktop_requested", "desktop_selected",
        "launch_requested", "browser_running", "login_requested", "authenticated", "complete",
    }:
        result["phase"] = value["phase"]
    if value.get("placementStatus") in {"verified", "mismatch", "unknown"}:
        result["placementStatus"] = value["placementStatus"]
    return result


def metric_summary(fixture):
    with fixture.LOCK:
        events = list(fixture.EVENTS)
    actions = {"email", "use-password", "password", "authenticator"}
    counts = Counter(e["action"] for e in events if e.get("action") in actions and e.get("accepted") is True)
    return {
        "acceptedActionCounts": {action: counts[action] for action in sorted(actions)},
        "authenticatorSecretTransmitted": any(e.get("authenticatorSecretTransmitted") is True for e in events),
        "allLoginStepsAccepted": all(counts[action] == 1 for action in actions),
    }


def execute(args, proof):
    for path in (ROOT.parent / "hybrid-control", ROOT.parent / "proxy-tools"):
        sys.path.insert(0, str(path))
    from manager_discovery import ensure_manager

    try:
        origin, session = ensure_manager(args.manager_url, start=False)
    except Exception:
        raise AcceptanceError("studio_unavailable") from None
    if session.get("authenticated") is not True or session.get("role") != "administrator":
        raise AcceptanceError("studio_administrator_required")
    api = API(origin)
    original = api.call("/studio/scripts/" + args.script_id)
    original_digest = digest(original)
    original_runs_digest = digest(api.call("/studio/scripts/" + args.script_id + "/runs"))
    before, active_count, trash_count = profile_snapshot(api)
    proof.update({"preexistingProfileCount": len(before), "activeProfilesBefore": active_count,
                  "trashProfilesBefore": trash_count, "checkOnly": args.check_only,
                  "desktopCountBefore": desktop_count()})
    if args.check_only:
        definition = {
            "name": "Installed Studio desktop preflight " + uuid4().hex[:8],
            "code": PREFLIGHT_SOURCE, "inputs": [],
            "stages": [{"id": "preflight", "label": "Check desktop access"}],
            "max_runtime_seconds": 30,
        }
        inputs = {}
    else:
        from x_login import LAB_EMAIL, LAB_PASSWORD, LAB_SECRET

        definition = lab_definition(original)
        inputs = {
            "account_record": ":".join(("replay_test", LAB_PASSWORD, LAB_EMAIL, LAB_SECRET)),
            "profile_id": "", "proxy_library_id": args.proxy_id,
            "name": "Studio acceptance LAB " + uuid4().hex[:8], "current_desktop": False,
            "seed": 41, "minutes": "120",
        }
    stages = {item["id"] for item in definition.get("stages", [])}
    script = run = None
    finished = False
    fixture_context = contextlib.nullcontext(None) if args.check_only else local_fixture()
    try:
        with fixture_context as fixture:
            script = api.call("/studio/scripts", definition)
            proof["testScriptId"] = script["id"]
            run = api.call("/studio/scripts/" + script["id"] + "/runs", {"inputs": inputs})
            inputs.clear()
            proof["testRunId"] = run["id"]
            try:
                run = wait_run(api, run["id"], min(args.timeout, 35) if args.check_only else args.timeout)
            except (AcceptanceError, KeyboardInterrupt):
                api.call("/studio/script-runs/" + run["id"] + "/stop", {})
                run = wait_run(api, run["id"], 20)
                raise
            finally:
                finished = run.get("status") not in ACTIVE
                proof["runStatus"] = run.get("status") if run.get("status") in STATUSES else "unknown"
                code = run.get("error_code")
                proof["errorCode"] = code if code in ERROR_CODES else "unknown_worker_failure" if code else None
                proof["stages"] = [event["stage"] for event in run.get("events", [])
                                   if event.get("kind") == "stage" and event.get("stage") in stages]
                receipt = safe_receipt(args.data_dir, run["id"])
                proof["receipt"] = receipt
                if fixture is not None:
                    proof.update(metric_summary(fixture))
            identifier = receipt.get("profileId")
            owned = identifier is not None and identifier not in before and receipt.get("lab") is True
            if owned:
                proof["testProfileRetainedOnFailure"] = True
            if run["status"] == "succeeded" and not args.check_only:
                if not owned or receipt.get("phase") != "complete" or receipt.get("authenticated") is not True:
                    raise AcceptanceError("completed_receipt_unconfirmed")
                if run.get("result", {}).get("profile_id") != identifier:
                    raise AcceptanceError("result_profile_mismatch")
                if proof["authenticatorSecretTransmitted"] or not proof["allLoginStepsAccepted"]:
                    raise AcceptanceError("fixture_authentication_unconfirmed")
                if (
                    receipt.get("placementVerified") is not True
                    or receipt.get("placementStatus") != "verified"
                    or type(receipt.get("selectedSpaceId")) is not int
                    or receipt.get("selectedSpaceId") != receipt.get("expectedSpaceId")
                ):
                    raise AcceptanceError("desktop_placement_unconfirmed")
                if (
                    type(proof.get("desktopCountBefore")) is not int
                    or desktop_count() != proof["desktopCountBefore"] + 1
                ):
                    raise AcceptanceError("desktop_creation_unconfirmed")
                current, _, _ = profile_snapshot(api)
                if not all(current.get(key) == value for key, value in before.items()):
                    raise AcceptanceError("preexisting_profile_changed")
                api.call("/profiles/" + identifier + "/close", {})
                proof["testProfileClosed"] = True
                api.call("/profiles/" + identifier, method="DELETE")
                proof["testProfileMovedToTrash"] = True
                proof["testProfileRetainedOnFailure"] = False
    finally:
        if script is not None and finished:
            try:
                api.call("/studio/scripts/" + script["id"] + "?revision=1", method="DELETE")
                proof["testDefinitionArchived"] = True
            except AcceptanceError:
                proof["testDefinitionArchived"] = False
        after, active_after, trash_after = profile_snapshot(api)
        proof["activeProfilesAfter"] = active_after
        proof["trashProfilesAfter"] = trash_after
        proof["desktopCountAfter"] = desktop_count()
        proof["preexistingProfilesUnchanged"] = all(after.get(key) == value for key, value in before.items())
        owned_id = proof.get("receipt", {}).get("profileId")
        proof["unexpectedNewProfileCount"] = len(set(after) - set(before) - {owned_id})
        proof["originalDefinitionUnchanged"] = digest(api.call("/studio/scripts/" + args.script_id)) == original_digest
        proof["originalRunHistoryUnchanged"] = digest(
            api.call("/studio/scripts/" + args.script_id + "/runs")
        ) == original_runs_digest
        receipt = proof.get("receipt", {})
        proof["desktopPlacementConfirmed"] = (
            receipt.get("placementVerified") is True and receipt.get("placementStatus") == "verified"
            and type(receipt.get("selectedSpaceId")) is int
            and receipt.get("selectedSpaceId") == receipt.get("expectedSpaceId")
        )
        proof["oneDesktopAdded"] = (
            type(proof.get("desktopCountBefore")) is int
            and proof.get("desktopCountAfter") == proof["desktopCountBefore"] + 1
        )
    proof["passed"] = (
        proof.get("runStatus") == "succeeded" and proof.get("testDefinitionArchived") is True
        and proof["preexistingProfilesUnchanged"] and proof["unexpectedNewProfileCount"] == 0
        and proof["originalDefinitionUnchanged"] and proof["originalRunHistoryUnchanged"]
        and (args.check_only or (
            proof.get("testProfileMovedToTrash") is True
            and proof["desktopPlacementConfirmed"] and proof["oneDesktopAdded"]
        ))
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager-url", default="http://127.0.0.1:50862")
    parser.add_argument("--script-id", default=SCRIPT_ID)
    parser.add_argument("--proxy-id", help="Saved Proxy library ID; required for the full lab run")
    parser.add_argument("--data-dir", type=Path,
                        default=Path.home() / "Library/Application Support/Camoufox Profile Manager")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--allow-marionette", action="store_true",
                        help="Explicitly run the legacy Gecko comparison; this is not external native control")
    args = parser.parse_args()
    if not args.check_only and not args.allow_marionette:
        parser.error("The full check uses legacy Marionette. Use an external native acceptance test for the requested controller.")
    if not args.check_only and (args.proxy_id is None or not PROFILE_ID.fullmatch(args.proxy_id)):
        parser.error("--proxy-id must contain eight letters or digits")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be between 30 and 1800 seconds")
    if not re.fullmatch(r"[a-fA-F0-9-]{36}", args.script_id):
        parser.error("--script-id must be a script UUID")
    args.data_dir = args.data_dir.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        parser.error("Choose a new writable --output file")
    proof = {"passed": False, "realAccountUsed": False, "authenticatorSecretTransmitted": False}
    try:
        execute(args, proof)
    except AcceptanceError as error:
        proof["harnessError"] = str(error)
    except KeyboardInterrupt:
        proof["harnessError"] = "acceptance_interrupted"
    except Exception:
        proof["harnessError"] = "acceptance_setup_failed"
    finally:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(proof, output, indent=2)
            output.write("\n")
        print(json.dumps(proof))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
