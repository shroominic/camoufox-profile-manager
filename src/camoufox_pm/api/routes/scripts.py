"""Administrator-owned database scripts and bounded, private execution requests."""

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from camoufox_pm.api.access import require_admin
from camoufox_pm.core import auth
from camoufox_pm.core.script_models import ScriptDefinition
from camoufox_pm.core.script_store import ScriptConflict, ScriptNotFound, public_run

router = APIRouter()
MAX_BODY_BYTES = 1024 * 1024


def _error(status: int, message: str):
    return HTTPException(status, message, headers={"Cache-Control": "no-store"})


async def _service(request: Request, response: Response):
    await require_admin()
    response.headers["Cache-Control"] = "no-store"
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise _error(403, "Scripts are available only through the local Studio app.")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get("X-Studio-Request") != "1":
            raise _error(403, "Use the Studio script client for this request.")
        origin = request.headers.get("Origin")
        if origin is not None and origin != str(request.base_url).rstrip("/"):
            raise _error(403, "Script requests must come from this Studio app.")
    service = getattr(request.app.state, "studio_scripts", None)
    if service is None:
        raise _error(503, "Studio script service is unavailable.")
    manager_url = getattr(service, "manager_url", None)
    if manager_url:
        try:
            expected_port = urlsplit(manager_url).port or 80
            actual_port = request.url.port or (443 if request.url.scheme == "https" else 80)
        except ValueError:
            raise _error(503, "Studio script service is unavailable.") from None
        if actual_port != expected_port:
            raise _error(403, "Script requests must use this Studio app's local port.")
    return service


async def _body(request: Request) -> dict:
    """Avoid Pydantic's default request-error echo for private input values."""
    if request.headers.get("Content-Type", "").partition(";")[0].strip().lower() != "application/json":
        raise _error(415, "Script requests require JSON.")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_BODY_BYTES:
            raise _error(413, "Script request exceeds the size limit.")
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise _error(422, "Supply a valid script request.") from None
    if not isinstance(value, dict):
        raise _error(422, "Supply a valid script request.")
    return value


def _definition(body: dict) -> ScriptDefinition:
    try:
        return ScriptDefinition.model_validate(body)
    except (ValidationError, ValueError, TypeError):
        raise _error(422, "The script definition is invalid. Check its code and input form.") from None


def _script(service, identifier: str) -> dict:
    value = service.store.get_script(identifier)
    if not value:
        raise _error(404, "Script not found.")
    return value


def _run(service, identifier: str) -> dict:
    value = service.store.get_run(identifier)
    if not value:
        raise _error(404, "Script run not found.")
    return value


def _manager_headers(request: Request) -> dict[str, str]:
    """Forward only the credential that authenticated this request, in memory."""
    principal = getattr(request.state, "principal", None)
    if getattr(principal, "kind", None) == "legacy_api_key":
        value = request.headers.get("X-API-Key")
        return {"X-API-Key": value} if value else {}
    if getattr(principal, "kind", None) == "session":
        value = request.cookies.get(auth.SESSION_COOKIE)
        return {"Cookie": f"{auth.SESSION_COOKIE}={value}"} if value else {}
    return {}


@router.get("/studio/scripts/capabilities", operation_id="studio_script_capabilities")
async def capabilities(request: Request, response: Response):
    service = await _service(request, response)
    return service.capabilities()


@router.get("/studio/scripts", operation_id="list_studio_scripts")
async def list_scripts(request: Request, response: Response):
    service = await _service(request, response)
    return {"scripts": service.store.list_scripts()}


@router.post("/studio/scripts", status_code=201, operation_id="create_studio_script")
async def create_script(request: Request, response: Response):
    service = await _service(request, response)
    definition = _definition(await _body(request))
    try:
        return service.store.save_script(definition)
    except ScriptConflict as exc:
        raise _error(409, str(exc)) from None


@router.get("/studio/scripts/{script_id}", operation_id="get_studio_script")
async def get_script(script_id: str, request: Request, response: Response):
    return _script(await _service(request, response), script_id)


@router.put("/studio/scripts/{script_id}", operation_id="update_studio_script")
async def update_script(script_id: str, request: Request, response: Response):
    service = await _service(request, response)
    _script(service, script_id)
    body = await _body(request)
    revision = body.pop("revision", None)
    if type(revision) is not int or revision < 1:
        raise _error(422, "Supply the current script revision.")
    definition = _definition(body)
    try:
        return service.store.save_script(definition, id=script_id, expected_revision=revision)
    except ScriptConflict as exc:
        raise _error(409, str(exc)) from None


@router.delete("/studio/scripts/{script_id}", operation_id="archive_studio_script")
async def archive_script(script_id: str, request: Request, response: Response):
    service = await _service(request, response)
    _script(service, script_id)
    try:
        revision = request.query_params.get("revision", "")
        if not revision.isascii() or not revision.isdecimal() or int(revision) < 1:
            raise ValueError
        service.store.archive_script(script_id, int(revision))
    except ScriptConflict as exc:
        raise _error(409, str(exc)) from None
    except (ValueError, TypeError):
        raise _error(422, "Supply the current script revision.") from None
    return {"success": True}


@router.get("/studio/scripts/{script_id}/runs", operation_id="list_studio_script_runs")
async def script_runs(script_id: str, request: Request, response: Response):
    service = await _service(request, response)
    _script(service, script_id)
    return {"runs": [public_run(value) for value in service.store.list_runs(script_id)]}


@router.post("/studio/scripts/{script_id}/runs", status_code=202, operation_id="start_studio_script")
async def start_script(script_id: str, request: Request, response: Response):
    service = await _service(request, response)
    _script(service, script_id)
    body = await _body(request)
    if set(body) - {"inputs", "resume_of"} or not isinstance(body.get("inputs", {}), dict):
        raise _error(422, "Supply valid script inputs.")
    resume_of = body.get("resume_of")
    if resume_of is not None and not isinstance(resume_of, str):
        raise _error(422, "Select a valid recovery run.")
    try:
        value = await service.start(
            script_id, body.get("inputs", {}), resume_of=resume_of,
            manager_headers=_manager_headers(request),
        )
    except ScriptNotFound:
        raise _error(404, "Script or recovery run not found.") from None
    except ScriptConflict:
        raise _error(409, "The script cannot start now. Reload its state before retrying.") from None
    except (ValueError, TypeError):
        raise _error(422, "The script inputs are invalid. Check required fields and choices.") from None
    except Exception:
        raise _error(503, "The script could not start. No private input details were saved.") from None
    return public_run(value)


@router.get("/studio/script-runs", operation_id="list_all_studio_script_runs")
async def all_runs(request: Request, response: Response):
    service = await _service(request, response)
    return {"runs": [public_run(value) for value in service.store.list_runs()]}


@router.get("/studio/script-runs/{run_id}", operation_id="get_studio_script_run")
async def get_run(run_id: str, request: Request, response: Response):
    service = await _service(request, response)
    return public_run(_run(service, run_id))


@router.post("/studio/script-runs/{run_id}/stop", operation_id="stop_studio_script_run")
async def stop_run(run_id: str, request: Request, response: Response):
    service = await _service(request, response)
    _run(service, run_id)
    try:
        return public_run(await service.stop(run_id))
    except ScriptConflict:
        raise _error(409, "The script run has already finished.") from None
    except Exception:
        raise _error(503, "The stop request could not be confirmed. Check the run state.") from None
