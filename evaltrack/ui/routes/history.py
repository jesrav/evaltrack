"""The cross-run view: how reliably each case passes, and how its scores move."""

from collections.abc import Callable

from fastapi import APIRouter

from evaltrack.repositories import RunRepository
from evaltrack.views.mainline import (
    NoMainline,
    load_readable_run,
    load_run_history,
)
from evaltrack.views.models import RunHistory


def build_history_router(
    resolve: Callable[[str], RunRepository], remote: RunRepository | NoMainline
) -> APIRouter:
    """`remote` is the repository whose `baseline` history is the mainline, or why no
    mount supplies one. Without it there is nothing to measure over."""
    router = APIRouter(prefix="/api/repositories/{slug}")

    @router.get("/history")
    def get_history(  # pyright: ignore[reportUnusedFunction]
        slug: str, run_id: str | None = None
    ) -> RunHistory:
        # `run_id` draws the viewed run over the history without folding it
        # into the rate.
        repo = resolve(slug)
        if isinstance(remote, NoMainline):
            return RunHistory()
        viewed_run = load_readable_run(repo, run_id) if run_id is not None else None
        return load_run_history(remote, viewed_run=viewed_run)

    return router
