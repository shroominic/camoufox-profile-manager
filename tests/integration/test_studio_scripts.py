"""The native Scripts API executes database state through a real private worker."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from camoufox_pm.api.routes import scripts
from camoufox_pm.core.script_runtime import ScriptService
from camoufox_pm.core.script_store import ScriptStore


@pytest.fixture
async def script_client(tmp_path, monkeypatch):
    async def administrator():
        return None

    monkeypatch.setattr(scripts, "require_admin", administrator)
    app = FastAPI()
    service = ScriptService(ScriptStore(tmp_path / "studio-control.sqlite3"), "http://127.0.0.1:12345")
    app.state.studio_scripts = service
    app.include_router(scripts.router, prefix="/api/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=service.manager_url,
        headers={"X-Studio-Request": "1"},
    ) as client:
        yield SimpleNamespace(client=client, service=service, root=tmp_path)
    await service.shutdown()


def definition(name, number):
    return {
        "name": name,
        "code": f"context.stage('check')\nprint(context.inputs['private_value'])\ncontext.set_result({{'answer':{number}}})",
        "inputs": [{"name": "private_value", "label": "Private value", "type": "secret", "required": True}],
        "stages": [{"id": "check", "label": "Check"}],
    }


async def wait_run(client, identifier):
    for _ in range(100):
        response = await client.get("/api/v1/studio/script-runs/" + identifier)
        assert response.status_code == 200
        value = response.json()
        if value["status"] not in {"queued", "running", "stopping"}:
            return value
        await asyncio.sleep(.03)
    pytest.fail("temporary worker did not finish")


async def test_other_scripts_are_just_database_definitions(script_client):
    client = script_client.client
    private = "PRIVATE-HTTP-INPUT-DO-NOT-SAVE"
    for name, number in (("First temporary script", 1), ("Second temporary script", 2)):
        created = await client.post("/api/v1/studio/scripts", json=definition(name, number))
        assert created.status_code == 201
        script = created.json()
        started = await client.post(
            f"/api/v1/studio/scripts/{script['id']}/runs", json={"inputs": {"private_value": private}}
        )
        assert started.status_code == 202
        result = await wait_run(client, started.json()["id"])
        assert result["status"] == "succeeded"
        assert result["result"] == {"answer": number}
        assert "snapshot" not in result and "code" not in result
        assert private not in str(result)
    listed = (await client.get("/api/v1/studio/scripts")).json()["scripts"]
    assert len(listed) == 2
    assert all("code" not in script for script in listed)
    for path in script_client.root.rglob("*"):
        if path.is_file():
            assert private.encode() not in path.read_bytes()


async def test_invalid_private_request_is_not_reflected(script_client):
    created = await script_client.client.post("/api/v1/studio/scripts", json=definition("Private input check", 1))
    identifier = created.json()["id"]
    private = "PRIVATE-MALFORMED-REQUEST-DO-NOT-ECHO"
    response = await script_client.client.post(
        f"/api/v1/studio/scripts/{identifier}/runs", json={"inputs": {"private_value": {"bad": private}}}
    )
    assert response.status_code == 422
    assert private not in response.text
    assert script_client.service.store.list_runs() == []
