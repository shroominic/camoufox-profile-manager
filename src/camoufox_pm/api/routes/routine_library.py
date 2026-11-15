"""The Studio routine library exposes catalog summaries, never browser input."""

import asyncio

from fastapi import APIRouter, Response

from camoufox_pm.api.access import require_admin
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.config import get_settings
from camoufox_pm.core.routine_library import read_catalog

router = APIRouter()


@router.get("/routine-library", operation_id="get_routine_library")
async def get_routine_library(response: Response):
    await require_admin()
    response.headers["Cache-Control"] = "no-store"
    summary = await asyncio.to_thread(read_catalog, get_settings().routine_catalog_path)
    return ApiResponse(success=True, data=summary, message="Read-only routine library")
