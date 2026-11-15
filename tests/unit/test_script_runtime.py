"""Execute real child workers against temporary state; no browsers or accounts."""

import asyncio
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from camoufox_pm.core.script_models import ScriptDefinition
from camoufox_pm.core.script_runtime import ScriptService
from camoufox_pm.core.script_store import ScriptConflict, ScriptStore

PRIVATE = "demo-user:PRIVATE-PASSWORD-NOT-SAVED:private@example.invalid:IGNORED-PRIVATE:BASE32-PRIVATE"


def definition(code, **changes):
    value = {
        "name": "Temporary runner check", "language": "python", "code": code,
        "inputs": [{"name": "account", "label": "Account", "type": "secret", "required": True}],
        "stages": [{"id": "validate", "label": "Validate"}, {"id": "work", "label": "Work"}],
        "max_runtime_seconds": 15,
    }
    return ScriptDefinition(**{**value, **changes})


@pytest.fixture
def runtime(tmp_path):
    store = ScriptStore(tmp_path / "studio-control.sqlite3")
    return ScriptService(store, "http://127.0.0.1:12345")


async def finished(service, run_id):
    task = service._tasks.get(run_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), timeout=15)
    return service.store.get_run(run_id)


def assert_private_absent(root):
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            for value in PRIVATE.split(":"):
                assert value.encode() not in data


async def test_db_script_executes_and_secret_streams_are_never_persisted(runtime):
    script = runtime.store.save_script(definition(
        "import sys\ncontext.stage('validate')\n"
        "print(context.inputs['account'])\nprint(context.inputs['account'],file=sys.stderr)\n"
        "context.set_result({'profile_id':'abcd1234','authenticated':True,"
        "'password':context.inputs['account'].split(':')[1],"
        "'echo':context.inputs['account'].split(':')[1]})\n"
    ))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    result = await finished(runtime, run["id"])
    assert result["status"] == "succeeded"
    assert result["result"] == {"profile_id": "abcd1234", "authenticated": True}
    assert [event["kind"] for event in result["events"]] == ["queued", "started", "stage", "succeeded"]
    assert_private_absent(runtime.store.path.parent)


async def test_arbitrary_exception_is_fixed_error_and_contains_no_input(runtime):
    script = runtime.store.save_script(definition("raise RuntimeError(context.inputs['account'])"))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    result = await finished(runtime, run["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "script_error"
    assert_private_absent(runtime.store.path.parent)


async def test_unstructured_raw_fd_output_fails_without_storing_it(runtime):
    script = runtime.store.save_script(definition(
        "import os\nos.write(1,context.inputs['account'].encode()+b'\\n')"
    ))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    result = await finished(runtime, run["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "worker_protocol_error"
    assert_private_absent(runtime.store.path.parent)


async def test_invalid_inputs_create_no_run(runtime):
    script = runtime.store.save_script(definition("context.stage('validate')"))
    with pytest.raises(ValueError):
        await runtime.start(script["id"], {"account": {"value": PRIVATE}})
    assert runtime.store.list_runs() == []


async def test_edit_does_not_change_running_source_snapshot(runtime):
    script = runtime.store.save_script(definition("import time\ntime.sleep(.2)\ncontext.set_result({'version':1})"))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    runtime.store.save_script(definition("context.set_result({'version':2})"), id=script["id"], expected_revision=1)
    result = await finished(runtime, run["id"])
    assert result["result"] == {"version": 1}
    assert result["revision"] == 1


async def test_stop_releases_worker_and_prevents_overlapping_runs(runtime):
    script = runtime.store.save_script(definition("import time\ncontext.stage('work')\ntime.sleep(60)"))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    with pytest.raises(ScriptConflict):
        await runtime.start(script["id"], {"account": PRIVATE})
    await asyncio.sleep(.2)
    await runtime.stop(run["id"])
    result = await finished(runtime, run["id"])
    assert result["status"] == "cancelled"
    assert result["error_code"] == "stopped"
    assert not runtime._processes
    assert not runtime._tasks


async def test_timeout_is_bounded_and_does_not_auto_retry(runtime):
    script = runtime.store.save_script(definition("import time\ntime.sleep(60)", max_runtime_seconds=5))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    result = await finished(runtime, run["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "timeout"
    assert len(runtime.store.list_runs()) == 1


async def test_worker_does_not_inherit_provider_keys(runtime, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", PRIVATE)
    monkeypatch.setenv("CPM_SECRET_KEY", PRIVATE)
    script = runtime.store.save_script(definition(
        "import os\ncontext.set_result({'provider_present':'OPENROUTER_API_KEY' in os.environ,"
        "'key_present':'CPM_SECRET_KEY' in os.environ})"
    ))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    result = await finished(runtime, run["id"])
    assert result["status"] == "succeeded"
    assert result["result"] == {"provider_present": False, "key_present": False}


async def test_transient_manager_auth_is_not_returned_or_stored(runtime):
    private_header = "TRANSIENT-MANAGER-AUTH-NOT-SAVED"
    script = runtime.store.save_script(definition(
        "context.set_result({'forwarded':bool(context._headers),'echo':context._headers['X-API-Key']})"
    ))
    run = await runtime.start(script["id"], {"account": PRIVATE}, manager_headers={"X-API-Key": private_header})
    result = await finished(runtime, run["id"])
    assert result["status"] == "succeeded"
    assert result["result"] == {"forwarded": True}
    for path in runtime.store.path.parent.rglob("*"):
        if path.is_file():
            assert private_header.encode() not in path.read_bytes()


async def test_missing_interpreter_has_fixed_failure(runtime):
    runtime.interpreter = str(runtime.root / "does-not-exist")
    script = runtime.store.save_script(definition("pass"))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    result = await finished(runtime, run["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "worker_unavailable"


async def test_explicit_resume_uses_same_revision_and_previous_private_directory(runtime):
    script = runtime.store.save_script(definition(
        "if context.resume_dir is None:\n context.fail('preflight_failed')\n"
        "context.set_result({'resumed':context.resume_dir is not None})"
    ))
    first = await runtime.start(script["id"], {"account": PRIVATE})
    await finished(runtime, first["id"])
    second = await runtime.start(script["id"], {"account": PRIVATE}, resume_of=first["id"])
    result = await finished(runtime, second["id"])
    assert result["status"] == "succeeded"
    assert result["result"] == {"resumed": True}
    assert_private_absent(runtime.store.path.parent)


async def test_shutdown_stops_running_input_worker(runtime):
    script = runtime.store.save_script(definition("import time\ntime.sleep(60)"))
    run = await runtime.start(script["id"], {"account": PRIVATE})
    await runtime.shutdown()
    assert runtime.store.get_run(run["id"])["status"] == "cancelled"
    assert runtime.capabilities()["available"] is False


async def test_stop_during_spawn_does_not_deliver_source_or_inputs(runtime, monkeypatch):
    script = runtime.store.save_script(definition(
        "(context.run_dir/'unexpected-action').write_text('changed')"
    ))
    entered, release = asyncio.Event(), asyncio.Event()
    actual_spawn = asyncio.create_subprocess_exec

    async def delayed_spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        return await actual_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    run = await runtime.start(script["id"], {"account": PRIVATE})
    await asyncio.wait_for(entered.wait(), timeout=3)
    await runtime.stop(run["id"])
    release.set()
    result = await finished(runtime, run["id"])
    assert result["status"] == "cancelled"
    assert not (runtime.root / run["id"] / "unexpected-action").exists()
    assert_private_absent(runtime.store.path.parent)


async def test_start_failure_releases_lock_even_if_status_write_fails(runtime, monkeypatch):
    script = runtime.store.save_script(definition("pass"))
    original_mkdir = Path.mkdir

    def failed_directory(path, *args, **kwargs):
        if path.parent == runtime.root:
            raise OSError("test directory failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failed_directory)
    monkeypatch.setattr(runtime.store, "update_run", Mock(side_effect=OSError("test DB failure")))
    with pytest.raises(OSError):
        await runtime.start(script["id"], {"account": PRIVATE})
    descriptor = runtime._lock()
    os.close(descriptor)
