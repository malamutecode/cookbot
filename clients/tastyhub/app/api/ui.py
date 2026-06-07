import dataclasses

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.tenant import TASTYHUB_CONFIG

router = APIRouter()


@router.get("/ui-strings")
async def ui_strings() -> JSONResponse:
    return JSONResponse(dataclasses.asdict(TASTYHUB_CONFIG.ui))
