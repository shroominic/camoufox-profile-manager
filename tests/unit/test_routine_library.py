"""The routine catalog boundary is read-only and exposes no recorded content."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from camoufox_pm.api.routes import routine_library as route
from camoufox_pm.core import routine_library as library


def catalog():
    return {
        "version": 1,
        "recordingsRoot": "/PRIVATE/recordings",
        "inventory": [{"profileId": "PRIVATE-ID", "password": "PRIVATE-PASSWORD"}],
        "routines": [
            {
                "id": "scroll",
                "title": "PRIVATE-PAGE-TITLE",
                "description": "PRIVATE-FIELD-VALUE",
                "candidateCount": 6,
                "variants": [
                    {
                        "id": "PRIVATE-VARIANT",
                        "source": {"session": "/PRIVATE/session"},
                        "workflow": "/PRIVATE/clip.json",
                        "length": "short",
                        "direction": "down",
                        "durationSeconds": 0.5,
                        "status": "ready",
                        "replayReady": True,
                    },
                    {
                        "length": "short",
                        "direction": "down",
                        "durationSeconds": 1.2,
                        "status": "ready",
                        "replayReady": True,
                    },
                    {
                        "length": "long",
                        "direction": "up",
                        "durationSeconds": 20,
                        "status": "needs-review",
                        "replayReady": False,
                    },
                ],
            },
            {"id": "login", "status": "parameterized", "variants": [], "candidateCount": 0},
        ],
    }


def test_summary_uses_only_allowlisted_labels_and_numeric_aggregates():
    result = library.summarize_catalog(catalog())
    assert result["status"] == "ready"
    assert result["routineCount"] == 2
    assert result["variantCount"] == 3
    assert result["readyCount"] == 2
    assert result["reviewCount"] == 1
    assert result["candidateCount"] == 6
    scroll = next(item for item in result["routines"] if item["key"] == "scroll")
    assert scroll["title"] == "Scroll with recorded pauses"
    assert next(pool for pool in scroll["pools"] if pool["length"] == "short") == {
        "length": "short", "direction": "down", "status": "ready", "count": 2,
        "minSeconds": 0.5, "maxSeconds": 1.2,
    }
    assert "PRIVATE" not in json.dumps(result)
    assert next(item for item in result["routines"] if item["key"] == "login")["parameterized"]


def test_catalog_read_does_not_write_or_follow_clip_paths(tmp_path):
    source = tmp_path / "catalog.json"
    source.write_text(json.dumps(catalog()))
    before = source.read_bytes(), source.stat().st_mtime_ns
    result = library.read_catalog(str(source))
    assert result["variantCount"] == 3
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert list(tmp_path.iterdir()) == [source]


def test_catalog_not_configured_or_missing_does_not_create_files(tmp_path):
    assert library.read_catalog(None)["status"] == "not-configured"
    assert library.read_catalog(str(tmp_path / "missing"))["status"] == "missing"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload", ["PRIVATE BAD JSON", '{"version": 2}', '[]'])
def test_malformed_catalog_has_safe_empty_response(tmp_path, payload):
    source = tmp_path / "PRIVATE.json"
    source.write_text(payload)
    result = library.read_catalog(str(source))
    assert result["status"] == "invalid"
    assert result["routines"] == []
    assert "PRIVATE" not in json.dumps(result)


def test_catalog_read_is_size_bounded(tmp_path, monkeypatch):
    source = tmp_path / "catalog.json"
    source.write_text(json.dumps(catalog()))
    monkeypatch.setattr(library, "MAX_CATALOG_BYTES", 10)
    assert library.read_catalog(str(source))["status"] == "invalid"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, "PRIVATE", True])
def test_invalid_numeric_metadata_is_rejected(value):
    document = catalog()
    document["routines"][0]["variants"][0]["durationSeconds"] = value
    with pytest.raises(ValueError):
        library.summarize_catalog(document)


def test_mismatched_readiness_remains_review_only():
    document = catalog()
    document["routines"][0]["variants"][0]["replayReady"] = "true"
    result = library.summarize_catalog(document)
    assert result["readyCount"] == 1
    assert result["reviewCount"] == 2


@pytest.mark.parametrize("key", ["PRIVATE-ACCOUNT", "scroll"])
def test_unknown_or_duplicate_taxonomy_is_rejected(key):
    document = catalog()
    document["routines"].append({"id": key, "variants": []})
    with pytest.raises(ValueError):
        library.summarize_catalog(document)


async def test_admin_route_is_no_store_and_reads_configured_catalog(monkeypatch):
    app = FastAPI()
    app.include_router(route.router)
    calls = []

    async def admin():
        calls.append("admin")

    monkeypatch.setattr(route, "require_admin", admin)
    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(routine_catalog_path="configured"))
    monkeypatch.setattr(route, "read_catalog", lambda path: {"status": "missing"} if path == "configured" else {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.get("/routine-library")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["data"]["status"] == "missing"
    assert calls == ["admin"]


async def test_non_admin_cannot_read_catalog(monkeypatch):
    app = FastAPI()
    app.include_router(route.router)

    async def denied():
        raise HTTPException(403, "Administrator access required")

    def must_not_read(_):
        pytest.fail("unauthorized request read local catalog")

    monkeypatch.setattr(route, "require_admin", denied)
    monkeypatch.setattr(route, "read_catalog", must_not_read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.get("/routine-library")
    assert result.status_code == 403
