"""Script APIs require admin and never echo or persist private run inputs."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from camoufox_pm.api.routes import scripts as routes
from camoufox_pm.core.script_models import validate_run_inputs
from camoufox_pm.core.script_store import ScriptStore


def definition():
    return {
        "name": "Test script", "code": "context.stage('work')",
        "inputs": [{"name": "account", "label": "Account", "type": "secret", "required": True}],
        "stages": [{"id": "work", "label": "Working"}],
    }


@pytest.fixture
def api(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    store = ScriptStore(tmp_path / "studio.db")
    calls = []

    class Service:
        def __init__(self):
            self.store = store

        def capabilities(self):
            return {"available": True, "language": "python"}

        async def start(self, script_id, inputs, resume_of=None, manager_headers=None):
            script = store.get_script(script_id)
            validate_run_inputs(script, inputs)
            calls.append({"inputs": inputs, "headers": manager_headers})
            return store.create_run(script, resume_of=resume_of)

        async def stop(self, run_id):
            return store.update_run(run_id, status="cancelled")

    service = Service()
    app.state.studio_scripts = service

    async def administrator():
        return None

    monkeypatch.setattr(routes, "require_admin", administrator)
    return app, service, calls


async def test_generic_definition_create_edit_archive_and_capabilities(api):
    app, _, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        created = await client.post("/studio/scripts", json=definition())
        assert created.status_code == 201
        script = created.json()
        listing = await client.get("/studio/scripts")
        assert listing.headers["cache-control"] == "no-store"
        assert "code" not in listing.json()["scripts"][0]
        assert (await client.get("/studio/scripts/capabilities")).json()["available"]
        assert (await client.get("/studio/scripts/" + script["id"])).json()["code"] == script["code"]
        changed = {**definition(), "revision": 1, "name": "Updated"}
        saved = await client.put("/studio/scripts/" + script["id"], json=changed)
        assert saved.json()["revision"] == 2
        stale = await client.put("/studio/scripts/" + script["id"], json=changed)
        assert stale.status_code == 409
        archived = await client.delete("/studio/scripts/" + script["id"] + "?revision=2")
        assert archived.status_code == 200
        assert (await client.get("/studio/scripts")).json() == {"scripts": []}


async def test_run_private_input_is_only_in_memory_and_public_run_has_no_snapshot(api):
    app, service, calls = api
    script = service.store.save_script(definition())
    secret = "private-account-sentinel"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        response = await client.post(f"/studio/scripts/{script['id']}/runs", json={"inputs": {"account": secret}})
        assert response.status_code == 202
        run_id = response.json()["id"]
        for path in (f"/studio/script-runs/{run_id}", "/studio/script-runs", f"/studio/scripts/{script['id']}/runs"):
            value = await client.get(path)
            assert secret not in value.text
            assert '"snapshot"' not in value.text
            assert '"code"' not in value.text
        stopped = await client.post(f"/studio/script-runs/{run_id}/stop")
        assert stopped.json()["status"] == "cancelled"
    assert calls[0]["inputs"]["account"] == secret
    for path in service.store.path.parent.iterdir():
        assert secret.encode() not in path.read_bytes()


@pytest.mark.parametrize("payload", [
    {"inputs": {"account": "private-account-sentinel"}, "extra": "private-account-sentinel"},
    {"inputs": ["private-account-sentinel"]},
    {"inputs": {"account": "private-account-sentinel"}, "resume_of": {"private-account-sentinel": 1}},
    {"inputs": {"unexpected": "private-account-sentinel"}},
])
async def test_invalid_run_errors_do_not_echo_input(api, payload):
    app, service, _ = api
    script = service.store.save_script(definition())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        response = await client.post(f"/studio/scripts/{script['id']}/runs", json=payload)
    assert response.status_code == 422
    assert "private-account-sentinel" not in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_arbitrary_runtime_exception_is_not_returned(api):
    app, service, _ = api
    script = service.store.save_script(definition())

    async def failed(*args, **kwargs):
        raise RuntimeError("private-account-sentinel")

    service.start = failed
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        response = await client.post(f"/studio/scripts/{script['id']}/runs", json={"inputs": {"account": "private-account-sentinel"}})
    assert response.status_code == 503
    assert "private-account-sentinel" not in response.text


async def test_admin_guard_precedes_store_and_private_body_read(api, monkeypatch):
    app, _, _ = api

    async def denied():
        raise HTTPException(403, "Administrator access required")

    monkeypatch.setattr(routes, "require_admin", denied)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        for method, path in [("GET", "/studio/scripts"), ("POST", "/studio/scripts"), ("POST", "/studio/scripts/missing/runs"), ("GET", "/studio/script-runs"), ("POST", "/studio/script-runs/missing/stop")]:
            assert (await client.request(method, path, content="invalid-private-json")).status_code == 403


@pytest.mark.parametrize("kind", ["session", "legacy_api_key", "anonymous_local"])
async def test_only_current_authentication_credential_is_forwarded(api, kind):
    app, service, calls = api
    script = service.store.save_script(definition())

    @app.middleware("http")
    async def principal(request, call_next):
        request.state.principal = SimpleNamespace(kind=kind)
        return await call_next(request)

    headers = {"X-API-Key": "private-api-sentinel", "Cookie": "cpm_session=private-session-sentinel; unrelated=do-not-forward"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        response = await client.post(f"/studio/scripts/{script['id']}/runs", headers=headers, json={"inputs": {"account": "private-account-sentinel"}})
    assert response.status_code == 202
    expected = {"session": {"Cookie": "cpm_session=private-session-sentinel"}, "legacy_api_key": {"X-API-Key": "private-api-sentinel"}, "anonymous_local": {}}
    assert calls[-1]["headers"] == expected[kind]
    assert "private-" not in json.dumps(response.json())


async def test_request_size_is_bounded_before_parse(api, monkeypatch):
    app, _, _ = api
    monkeypatch.setattr(routes, "MAX_BODY_BYTES", 10)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        response = await client.post("/studio/scripts", content='{"private-account-sentinel":true}', headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert "private-account-sentinel" not in response.text


async def test_missing_runner_returns_fixed_unavailable_response(api):
    app, _, _ = api
    app.state.studio_scripts = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        response = await client.get("/studio/scripts")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


async def test_mutation_requires_custom_header_and_same_origin(api):
    app, _, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        missing_header = await client.post("/studio/scripts", json=definition())
        assert missing_header.status_code == 403
        foreign = await client.post("/studio/scripts", json=definition(), headers={"X-Studio-Request": "1", "Origin": "https://foreign.invalid"})
        assert foreign.status_code == 403
        own = await client.post("/studio/scripts", json=definition(), headers={"X-Studio-Request": "1", "Origin": "http://127.0.0.1"})
        assert own.status_code == 201


async def test_text_plain_json_cannot_bypass_browser_preflight(api):
    app, _, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1", headers={"X-Studio-Request": "1"}) as client:
        response = await client.post("/studio/scripts", content=json.dumps(definition()), headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


async def test_non_loopback_host_cannot_access_local_code_executor(api):
    app, _, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://hostile.invalid", headers={"X-Studio-Request": "1"}) as client:
        assert (await client.get("/studio/scripts")).status_code == 403
        response = await client.post("/studio/scripts", json=definition(), headers={"Origin": "http://hostile.invalid"})
        assert response.status_code == 403


async def test_configured_manager_port_must_match_request_port(api):
    app, service, _ = api
    service.manager_url = "http://127.0.0.1:43210"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:43211") as client:
        assert (await client.get("/studio/scripts")).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:43210") as client:
        assert (await client.get("/studio/scripts")).status_code == 200
