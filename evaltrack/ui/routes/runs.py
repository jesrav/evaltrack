"""Reading, downloading and deleting one repository's runs."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from evaltrack.core.errors import RunReferencedError
from evaltrack.core.refs import BASELINE_REF, ReflogEntry
from evaltrack.core.run_record import (
    CaseRecord,
    RunRecord,
    dump_run_json,
    ensure_run_id,
)
from evaltrack.repositories import RunRepository, RunSummary, delete_run_if_unreferenced
from evaltrack.ui.models import MainlineEntry
from evaltrack.ui.routes import MAX_PAGE
from evaltrack.ui.run_cache import RunCache
from evaltrack.ui.run_view import dump_case_json, dump_run_view_json
from evaltrack.ui.security import reject_cross_origin_write


def _load_case_or_404(run: RunRecord, *, test: str, case: str) -> CaseRecord:
    recorded = run.tests.get(test)
    record = recorded.cases.get(case) if recorded is not None else None
    if record is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, f"case not found: {test} {case!r}")
    return record


def build_runs_router(
    resolve: Callable[[str], RunRepository],
    mainline: RunRepository | None,
    *,
    run_cache: RunCache,
) -> APIRouter:
    """`mainline` is the repository the promote history lives in, None when no
    mount supplies one."""
    router = APIRouter(prefix="/api/repositories/{slug}/runs")

    def load_run_or_404(slug: str, run_id: str) -> RunRecord:
        run = run_cache.load(slug, repo=resolve(slug), run_id=run_id)
        if run is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, f"run not found: {run_id}")
        return run

    @router.get("")
    def list_runs(  # pyright: ignore[reportUnusedFunction]
        slug: str,
        *,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[RunSummary]:
        repo = resolve(slug)
        out: list[RunSummary] = []
        for i, summary in enumerate(repo.list_runs()):
            if i < offset:
                continue
            if len(out) >= limit:
                break
            out.append(summary)
        return out

    @router.get("/{run_id}")
    def get_run(slug: str, run_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        # Returned as bytes. A returned model makes FastAPI serialize the run a
        # second time.
        return Response(
            content=dump_run_view_json(load_run_or_404(slug, run_id)),
            media_type="application/json",
        )

    @router.get("/{run_id}/cases")
    def get_case(  # pyright: ignore[reportUnusedFunction]
        slug: str, run_id: str, *, test: str, case: str
    ) -> Response:
        """One case, whole. The test and the case are query parameters, because
        a nodeid contains `::` and `/`."""
        record = _load_case_or_404(load_run_or_404(slug, run_id), test=test, case=case)
        return Response(content=dump_case_json(record), media_type="application/json")

    @router.get("/{run_id}/mainline")
    def get_run_mainline(  # pyright: ignore[reportUnusedFunction]
        slug: str, run_id: str
    ) -> MainlineEntry | None:
        # A promoted run keeps its id, so a local run resolves against the
        # remote mainline too.
        resolve(slug)  # 404 for unknown slugs, like the sibling endpoints
        # Nothing here reaches storage. The check stops a malformed id from
        # reading as a run that was never promoted.
        ensure_run_id(run_id)
        if mainline is None:
            return None
        match: ReflogEntry | None = None
        for entry in mainline.get_reflog(BASELINE_REF):  # oldest-first
            if entry.run_id == run_id:
                match = entry
        if match is None:
            return None
        return MainlineEntry(
            commit=match.commit,
            pr=match.pr,
            title=match.title,
            moved_at=match.moved_at,
        )

    @router.get("/{run_id}/download")
    def download_run(slug: str, run_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        # The whole run, byte-identical to the stored run when this version
        # recorded it.
        run = load_run_or_404(slug, run_id)
        return Response(
            content=dump_run_json(run),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    @router.delete("/{run_id}")
    def delete_run(  # pyright: ignore[reportUnusedFunction]
        slug: str, run_id: str, *, request: Request
    ) -> dict[str, str]:
        reject_cross_origin_write(request)
        repo = resolve(slug)
        try:
            deleted = delete_run_if_unreferenced(repo, run_id)
        except RunReferencedError as exc:
            raise HTTPException(
                HTTPStatus.CONFLICT,
                {"message": str(exc), "refs": exc.refs},
            ) from exc
        run_cache.drop(slug, run_ids=[run_id])
        if deleted is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, f"run not found: {run_id}")
        # Only the id. A summary needs a run-body read for data the delete makes
        # obsolete.
        return {"id": deleted}

    return router
