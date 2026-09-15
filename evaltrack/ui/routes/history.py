"""The cross-run view: how reliably each case passes, and how its scores move."""

from collections.abc import Callable

from fastapi import APIRouter

from evaltrack.repositories import RunRepository
from evaltrack.ui.models import RunHistory
from evaltrack.ui.views import load_run_history


def build_history_router(
    resolve: Callable[[str], RunRepository], mainline: RunRepository | None
) -> APIRouter:
    """`mainline` is the repository the promote history lives in, None when no
    mount supplies one. Without it there is nothing to measure over."""
    router = APIRouter(prefix="/api/repositories/{slug}")

    @router.get("/history")
    def get_history(  # pyright: ignore[reportUnusedFunction]
        slug: str, run_id: str | None = None
    ) -> RunHistory:
        # `run_id` draws the viewed run over the history without folding it
        # into the rate.
        return load_run_history(resolve(slug), mainline=mainline, run_id=run_id)

    return router
