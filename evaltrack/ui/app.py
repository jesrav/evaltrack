"""The FastAPI app. A JSON view of one or more repositories under `/api/`, and
the built frontend at `/`.

Each group of endpoints is a router factory in `evaltrack.ui.routes`. A factory
takes what its routes read and closes over it, so nothing reaches for the app.
"""

import importlib.metadata
from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from evaltrack.config import PrUrlTemplate
from evaltrack.core.errors import (
    CorruptRecordError,
    InvalidIdentifierError,
    RepositoryUnavailableError,
    UnsupportedSchemaError,
)
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import (
    MountedRepository,
    ProjectConfig,
    RepositoryInfo,
)
from evaltrack.ui.routes import static as static_routes
from evaltrack.ui.routes.history import build_history_router
from evaltrack.ui.routes.meta import build_meta_router
from evaltrack.ui.routes.refs import build_refs_router
from evaltrack.ui.routes.runs import build_runs_router
from evaltrack.ui.routes.static import build_static_router
from evaltrack.ui.run_cache import RunCache
from evaltrack.ui.security import (
    LOOPBACK_ALLOWED_HOSTS,
    SECURITY_HEADERS,
)


def _handle_storage_unavailable(
    request: Request, exc: RepositoryUnavailableError
) -> JSONResponse:
    # An upstream failure, not a bug.
    return JSONResponse(
        status_code=HTTPStatus.BAD_GATEWAY,
        content={"detail": f"storage backend error: {exc}"},
    )


def _handle_invalid_identifier(
    request: Request, exc: InvalidIdentifierError
) -> JSONResponse:
    # Malformed input, not a missing resource.
    return JSONResponse(
        status_code=HTTPStatus.BAD_REQUEST,
        content={"detail": f"invalid identifier: {exc}"},
    )


def _handle_corrupt_record(request: Request, exc: CorruptRecordError) -> JSONResponse:
    # The record exists but its bytes cannot be read. Not a 404, which the
    # client reads as deleted, and not a 500, which reads as a server fault.
    return JSONResponse(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        content={"detail": f"cannot be read: {exc}"},
    )


def _handle_unsupported_schema(
    request: Request, exc: UnsupportedSchemaError
) -> JSONResponse:
    # Not the 422 a damaged body gets: the run is whole, and it is this
    # server that lacks the format, so a client must not treat it as
    # something to repair or delete.
    return JSONResponse(
        status_code=HTTPStatus.NOT_IMPLEMENTED,
        content={"detail": f"recorded by another evaltrack: {exc}"},
    )


async def _add_security_headers(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


def create_app(
    repositories: Mapping[str, MountedRepository],
    *,
    pr_url_template: PrUrlTemplate | None = None,
) -> FastAPI:
    """Build a FastAPI app that serves the given repositories by slug.

    The app has no authentication. Anything that reaches it can read and delete
    in every mounted repository. Serve it on loopback only. The `Host` allowlist
    only defends a browser against DNS rebinding.

    Args:
        repositories: What to mount, keyed by slug. The slug appears in URLs
            unchanged.
        pr_url_template: Exposed at `/api/config`.

    Raises:
        ValueError: when `repositories` is empty, or when `pr_url_template` is
            not a usable link target.
    """
    if not repositories:
        raise ValueError("create_app needs at least one repository")

    project_config = ProjectConfig(pr_url_template=pr_url_template)

    # Copied, so a caller cannot mutate the routes mid-flight.
    mounts: dict[str, RunRepository] = {
        s: m.repository for s, m in repositories.items()
    }
    infos: dict[str, RepositoryInfo] = {
        s: RepositoryInfo(slug=s, url=m.url, role=m.role)
        for s, m in repositories.items()
    }
    # The mainline lives on the remote and nowhere else. A local `baseline` is
    # a developer's own promotion, not the team's, so no view reads it.
    mainline: RunRepository | None = next(
        (m.repository for m in repositories.values() if m.role == "remote"), None
    )

    def resolve(slug: str) -> RunRepository:
        try:
            return mounts[slug]
        except KeyError:
            raise HTTPException(
                HTTPStatus.NOT_FOUND, f"unknown repository: {slug!r}"
            ) from None

    app = FastAPI(
        title="evaltrack",
        version=importlib.metadata.version("evaltrack"),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_exception_handler(RepositoryUnavailableError, _handle_storage_unavailable)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(InvalidIdentifierError, _handle_invalid_identifier)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(CorruptRecordError, _handle_corrupt_record)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(UnsupportedSchemaError, _handle_unsupported_schema)  # pyright: ignore[reportArgumentType]

    app.include_router(build_static_router())
    app.include_router(build_meta_router(project_config, infos))
    # One cache for both routers, so that a delete in one clears what the
    # other read.
    run_cache = RunCache()
    app.include_router(
        build_runs_router(
            resolve, mainline, run_cache=run_cache, pr_url_template=pr_url_template
        )
    )
    app.include_router(build_refs_router(resolve, run_cache=run_cache))
    app.include_router(build_history_router(resolve, mainline))

    # Absent in development (Vite run separately) and in tests.
    assets_dir = static_routes.STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=LOOPBACK_ALLOWED_HOSTS,
        www_redirect=False,
    )

    # Added last, so it runs outermost and a rejected Host still gets the headers.
    app.middleware("http")(_add_security_headers)

    return app
