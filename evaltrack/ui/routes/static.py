"""The built frontend, and the fallback when it is not built."""

from http import HTTPStatus
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

STATIC_DIR = Path(__file__).parent.parent / "static"


def build_static_router() -> APIRouter:
    """The index and the favicon. Neither reads a repository."""
    router = APIRouter()
    # Read now, not at import, so a test can point STATIC_DIR at a temp build.
    index_html = STATIC_DIR / "index.html"
    favicon_svg = STATIC_DIR / "favicon.svg"

    @router.get("/", response_model=None)
    def serve_index() -> PlainTextResponse | FileResponse:  # pyright: ignore[reportUnusedFunction]
        # FastAPI cannot synthesize a schema for the union, hence response_model=None.
        # A rebuild changes the hashed asset names, and a cached index.html then
        # points at a 404 bundle, hence `no-cache`.
        if index_html.exists():
            return FileResponse(index_html, headers={"Cache-Control": "no-cache"})
        return PlainTextResponse(
            "evaltrack: the frontend is not built. Run `just frontend_build`.\n"
        )

    @router.get("/favicon.svg", include_in_schema=False)
    def serve_favicon() -> FileResponse:  # pyright: ignore[reportUnusedFunction]
        # Absent until the frontend is built.
        if not favicon_svg.exists():
            raise HTTPException(HTTPStatus.NOT_FOUND, "favicon not built")
        return FileResponse(favicon_svg)

    return router
