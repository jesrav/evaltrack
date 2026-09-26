"""Refs, their histories, and deleting one."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from evaltrack.core.errors import BaselineRefProtectedError, RefNotFoundError
from evaltrack.core.refs import ReflogEntry
from evaltrack.repositories import (
    RefDeletion,
    RunRepository,
    RunSummary,
    delete_ref_and_orphaned_runs,
)
from evaltrack.ui.models import RefListing, ReflogListing
from evaltrack.ui.routes import MAX_PAGE
from evaltrack.ui.run_cache import RunCache
from evaltrack.ui.security import reject_cross_origin_write
from evaltrack.ui.views import iter_ref_tips


class _SummaryCache:
    """Run summaries for one request, cached by id."""

    def __init__(self, repo: RunRepository) -> None:
        self._repo = repo
        self._seen: dict[str, RunSummary | None] = {}

    def get(self, run_id: str) -> RunSummary | None:
        # A deleted run, and one recorded by another evaltrack, is None from
        # the repository already.
        if run_id not in self._seen:
            self._seen[run_id] = self._repo.get_run_summary(run_id)
        return self._seen[run_id]


def build_refs_router(
    resolve: Callable[[str], RunRepository], *, run_cache: RunCache
) -> APIRouter:
    router = APIRouter(prefix="/api/repositories/{slug}")

    @router.get("/refs")
    def list_refs(slug: str) -> list[RefListing]:  # pyright: ignore[reportUnusedFunction]
        repo = resolve(slug)
        summaries = _SummaryCache(repo)
        return [
            RefListing(
                name=name,
                tip=tip,
                tip_run=summaries.get(tip.run_id) if tip else None,
                error=error,
            )
            for name, tip, error in iter_ref_tips(repo)
        ]

    @router.get("/refs/{name:path}")
    def get_ref(slug: str, name: str) -> ReflogEntry:  # pyright: ignore[reportUnusedFunction]
        repo = resolve(slug)
        ref = repo.get_ref(name)
        if ref is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, f"ref not found: {name}")
        return ref

    @router.get("/reflogs/{name:path}")
    def get_reflog(  # pyright: ignore[reportUnusedFunction]
        slug: str,
        name: str,
        *,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[ReflogListing]:
        # A top-level path, so the `:path`-typed ref name cannot collide with a
        # `/log` suffix.
        repo = resolve(slug)
        summaries = _SummaryCache(repo)
        return [
            ReflogListing(**entry.model_dump(), run=summaries.get(entry.run_id))
            for entry in repo.tail_reflog(name, offset + limit)[offset:]
        ]

    @router.delete("/refs/{name:path}")
    def delete_ref(  # pyright: ignore[reportUnusedFunction]
        slug: str, name: str, *, request: Request
    ) -> RefDeletion:
        reject_cross_origin_write(request)
        repo = resolve(slug)
        try:
            deletion = delete_ref_and_orphaned_runs(repo, name)
        except RefNotFoundError as exc:
            raise HTTPException(HTTPStatus.NOT_FOUND, f"ref not found: {name}") from exc
        except BaselineRefProtectedError as exc:
            raise HTTPException(
                HTTPStatus.CONFLICT,
                f"the reserved {name!r} ref keeps the mainline history alive, "
                "so the dashboard cannot delete it",
            ) from exc
        run_cache.drop(slug, run_ids=deletion.deleted_runs)
        return deletion

    return router
