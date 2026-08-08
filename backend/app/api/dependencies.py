from __future__ import annotations

from fastapi import Request

from ..storage import AnnotationStorage


def get_storage(request: Request) -> AnnotationStorage:
    return request.app.state.storage
