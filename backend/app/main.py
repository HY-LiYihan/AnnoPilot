from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api import annotations, audit, documents, engagement, exports, health, presets, runs, settings, suggestions, tags
from .storage import AnnotationStorage


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _cors_origins() -> list[str]:
    raw_value = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [origin.strip() for origin in raw_value.split(",") if origin.strip()]


def create_app(storage: AnnotationStorage | None = None, suggestion_reviewer=None, engagement_candidate_generator=None) -> FastAPI:
    storage = storage or AnnotationStorage(
        database_path=Path(os.getenv("DATABASE_PATH", "/data/runtime/annopilot.sqlite")),
        data_root=Path(os.getenv("DATA_ROOT", "/data/projects")),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage.initialize()
        yield

    app = FastAPI(title="AnnoPilot API", lifespan=lifespan)
    app.state.storage = storage
    app.state.suggestion_reviewer = suggestion_reviewer
    app.state.engagement_candidate_generator = engagement_candidate_generator
    cors_origins = _cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials="*" not in cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(settings.router)
    app.include_router(audit.router)
    app.include_router(presets.router)
    app.include_router(documents.router)
    app.include_router(runs.router)
    app.include_router(annotations.router)
    app.include_router(tags.router)
    app.include_router(suggestions.router)
    app.include_router(engagement.router)
    app.include_router(exports.router)

    static_dir = Path(os.getenv("STATIC_DIR", Path.cwd() / "static"))
    if _env_bool("SERVE_STATIC", True) and static_dir.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}", response_class=FileResponse)
        def spa(path: str) -> FileResponse | PlainTextResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found.")
            index_path = static_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path)
            return PlainTextResponse("AnnoPilot API is running.")

    return app


app = create_app()
