from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api import annotations, audit, documents, exports, health, runs, suggestions, tags
from .storage import AnnotationStorage


def create_app(storage: AnnotationStorage | None = None, suggestion_reviewer=None) -> FastAPI:
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(audit.router)
    app.include_router(documents.router)
    app.include_router(runs.router)
    app.include_router(annotations.router)
    app.include_router(tags.router)
    app.include_router(suggestions.router)
    app.include_router(exports.router)

    static_dir = Path(os.getenv("STATIC_DIR", Path.cwd() / "static"))
    if static_dir.exists():
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
