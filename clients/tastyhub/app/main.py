from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import search_prefs, sessions, shopping_list, spizarnia, ui, websocket
from app.config.settings import Settings, get_settings
from app.config.tenant import TASTYHUB_CONFIG
from cookbot.exceptions import SessionExpiredError, TenantNotFoundError
from cookbot.services.firestore import FirestoreService

log = structlog.get_logger()

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Allow tests to pre-populate app.state before startup
    if not hasattr(app.state, "settings"):
        app.state.settings = get_settings()
    cfg: Settings = app.state.settings

    # Expose to os.environ so SDKs that read env directly (OpenAI, Firestore) pick them up
    import os
    os.environ.setdefault("OPENAI_API_KEY", cfg.openai_api_key)
    if cfg.firestore_emulator_host:
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", cfg.firestore_emulator_host)

    if not hasattr(app.state, "firestore"):
        app.state.firestore = FirestoreService(
            project_id=cfg.google_cloud_project,
            database_id=cfg.firestore_database,
            tenant_id=cfg.tenant_id,
        )
    log.info("startup", tenant=cfg.tenant_id, version=VERSION)
    yield
    log.info("shutdown", tenant=cfg.tenant_id)


app = FastAPI(title="CookBot — TastyHub", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=TASTYHUB_CONFIG.allowed_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["x-api-key", "authorization", "content-type", "x-dev-uid"],
)

app.include_router(sessions.router, prefix="/v1")
app.include_router(spizarnia.router, prefix="/v1")
app.include_router(search_prefs.router, prefix="/v1")
app.include_router(shopping_list.router, prefix="/v1")
app.include_router(ui.router, prefix="/v1")
app.include_router(websocket.router, prefix="/v1")


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    return {
        "status": "ok",
        "tenant": request.app.state.settings.tenant_id,
        "version": VERSION,
    }


@app.exception_handler(TenantNotFoundError)
async def tenant_not_found_handler(request: Request, exc: TenantNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(SessionExpiredError)
async def session_expired_handler(request: Request, exc: SessionExpiredError) -> JSONResponse:
    return JSONResponse(status_code=410, content={"detail": str(exc)})
