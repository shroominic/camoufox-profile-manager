"""A dependency's fixed reason crosses the worker boundary without private text."""

import asyncio

import pytest

from camoufox_pm.core.script_models import ScriptDefinition
from camoufox_pm.core.script_runtime import ScriptService
from camoufox_pm.core.script_store import ScriptStore
from camoufox_pm.script_worker import ScriptFailure, exception_error_code, safe_error_code

LOGIN_CODES = (
    "login_target_changed", "login_field_focus", "login_field_type", "login_page_timeout",
    "login_manual_action", "login_state_unavailable",
)
PRIVATE = "PRIVATE-EXCEPTION-CONTENT-DO-NOT-STORE"


@pytest.mark.parametrize("code", LOGIN_CODES)
def test_known_dependency_reason_is_allowed(code):
    error = RuntimeError(PRIVATE)
    error.code = code
    assert exception_error_code(error) == code


@pytest.mark.parametrize("code", [None, 1, True, [PRIVATE], {"reason": PRIVATE}, PRIVATE])
def test_unknown_or_nonstring_reason_collapses_to_generic(code):
    assert safe_error_code(code) == "script_error"
    assert ScriptFailure(code).code == "script_error"


def test_failing_exception_property_does_not_cross_error_boundary():
    class PrivateFailure(RuntimeError):
        @property
        def code(self):
            raise RuntimeError(PRIVATE)

    assert exception_error_code(PrivateFailure(PRIVATE)) == "script_error"


@pytest.mark.parametrize(
    "kind, reason, expected",
    [
        ("dependency", "'login_field_focus'", "login_field_focus"),
        ("dependency", "context.inputs['account']", "script_error"),
        ("dependency", "[context.inputs['account']]", "script_error"),
        ("controlled", "context.inputs['account']", "script_error"),
    ],
)
async def test_real_worker_persists_only_fixed_reason(tmp_path, kind, reason, expected):
    store = ScriptStore(tmp_path / "studio-control.sqlite3")
    service = ScriptService(store, "http://127.0.0.1:12345")
    source = (
            "class DependencyFailure(RuntimeError): pass\n"
            "error=DependencyFailure(context.inputs['account'])\n"
            f"error.code={reason}\n"
            "raise error\n"
    )
    if kind == "controlled":
        source = (
            "from camoufox_pm.script_worker import ScriptFailure\n"
            "error=ScriptFailure('script_error')\n"
            f"error.code={reason}\n"
            "raise error\n"
        )
    definition = ScriptDefinition(
        name="Private dependency failure test",
        code=source,
        inputs=[{"name": "account", "label": "Account", "type": "secret", "required": True}],
    )
    script = store.save_script(definition)
    try:
        run = await service.start(script["id"], {"account": PRIVATE})
        task = service._tasks.get(run["id"])
        if task:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        result = store.get_run(run["id"])
        assert result["status"] == "failed"
        assert result["error_code"] == expected
        assert PRIVATE not in repr(result)
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert PRIVATE.encode() not in path.read_bytes()
    finally:
        await service.shutdown()
