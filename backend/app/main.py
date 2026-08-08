from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .storage import AnnotationStorage, DEFAULT_PROJECT_ID, NotFoundError, ValidationError


def create_app(storage: AnnotationStorage | None = None) -> FastAPI:
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/projects/{project_id}/import-txt")
    async def import_txt(project_id: str, file: Annotated[UploadFile, File()]) -> dict:
        if not file.filename.lower().endswith(".txt"):
            raise HTTPException(status_code=400, detail="Only .txt files are supported.")
        data = await file.read()
        try:
            return storage.import_txt(project_id, file.filename, data)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/documents/{document_id}")
    def get_document(project_id: str, document_id: str) -> dict:
        try:
            return storage.get_document(project_id, document_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/sentences/{sentence_id}/annotations")
    def create_annotation(
        project_id: str,
        sentence_id: str,
        payload: Annotated[dict, Body()],
    ) -> dict:
        try:
            annotations = storage.create_annotation(
                project_id=project_id,
                sentence_id=sentence_id,
                tag_id=str(payload["tag_id"]),
                start_token_index=int(payload["start_token_index"]),
                end_token_index=int(payload["end_token_index"]),
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"Missing field: {exc.args[0]}") from exc
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"annotations": annotations}

    @app.delete("/api/projects/{project_id}/annotations/{annotation_id}")
    def delete_annotation(project_id: str, annotation_id: str) -> dict[str, bool]:
        try:
            storage.delete_annotation(project_id, annotation_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @app.post("/api/projects/{project_id}/sentences/{sentence_id}/complete")
    def complete_sentence(project_id: str, sentence_id: str, payload: Annotated[dict, Body()]) -> dict[str, bool]:
        completed = bool(payload.get("completed", True))
        try:
            storage.set_sentence_completed(project_id, sentence_id, completed)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"completed": completed}

    @app.get("/api/projects/{project_id}/documents/{document_id}/export.jsonl")
    def export_document(project_id: str, document_id: str) -> StreamingResponse:
        try:
            lines = storage.export_document_lines(project_id, document_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        headers = {"Content-Disposition": f'attachment; filename="{document_id}.jsonl"'}
        return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)

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
