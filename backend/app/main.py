"""CC-monitor FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import orchestration_router, sessions_router
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan events."""
    settings = get_settings()
    logger.info("CC-monitor backend starting on port %s", settings.api_port)
    yield
    logger.info("CC-monitor backend shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()

    application = FastAPI(
        title="CC-monitor",
        description="Claude Code session monitoring backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        """Return the service health status."""
        return {"status": "ok"}

    application.include_router(sessions_router)
    application.include_router(orchestration_router)

    _mount_static(application)

    return application


def _mount_static(application: FastAPI) -> None:
    """Mount the frontend static build if the static directory exists."""
    static_path = os.path.abspath(_STATIC_DIR)
    if os.path.isdir(static_path):
        logger.info("Serving frontend static files from %s", static_path)
        application.mount(
            "/",
            StaticFiles(directory=static_path, html=True),
            name="static",
        )
    else:
        logger.info(
            "Static directory not found at %s — running in API-only mode",
            static_path,
        )


app = create_app()
