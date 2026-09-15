"""Reading, downloading and deleting one repository's runs."""

import logging
from collections.abc import Callable
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from evaltrack.config import PrUrlTemplate
from evaltrack.core.errors import RunReferencedError
from evaltrack.core.run_record import (
    RunRecord,
    dump_run_json,
    ensure_run_id,
    strip_raw_results,
)
from evaltrack.repositories import RunRepository, RunSummary, delete_run_if_unreferenced
from evaltrack.ui.models import MainlineEntry
from evaltrack.ui.report import collect_report_data, render_report
from evaltrack.ui.routes import MAX_PAGE
from evaltrack.ui.security import reject_cross_origin_write
from evaltrack.ui.views import find_mainline_entry

_logger = logging.getLogger(__name__)


def _load_run_or_404(repo: RunRepository, run_id: str) -> RunRecord:
    """`load_run` that raises 404 for a missing run."""
    run = repo.load_run(run_id)
    if run is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, f"run not found: {run_id}")
    return run


def build_runs_router(
    resolve: Callable[[str], RunRepository],
    mainline: RunRepository | None,
    *,
    pr_url_template: PrUrlTemplate | None = None,
) -> APIRouter:
    """`mainline` is the repository the promote history lives in, None when no
    mount supplies one. `pr_url_template` goes into the report, so its PR
    numbers link as the dashboard's do."""
    router = APIRouter(prefix="/api/repositories/{slug}/runs")

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
    def get_run(slug: str, run_id: str) -> RunRecord:  # pyright: ignore[reportUnusedFunction]
        repo = resolve(slug)
        return strip_raw_results(_load_run_or_404(repo, run_id))

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
        return find_mainline_entry(mainline, run_id)

    @router.get("/{run_id}/download")
    def download_run(slug: str, run_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        # The full run with `raw_results`, byte-identical to the stored run when
        # this version recorded it.
        repo = resolve(slug)
        run = _load_run_or_404(repo, run_id)
        return Response(
            content=dump_run_json(run),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    @router.get("/{run_id}/report")
    def download_report(  # pyright: ignore[reportUnusedFunction]
        slug: str,
        run_id: str,
        *,
        via: str | None = None,
        against: str | None = None,
        against_slug: str | None = None,
        against_via: str | None = None,
    ) -> Response:
        """The run as the single-file report, to hand to someone without the
        dashboard. `against` names a run to compare from, in `against_slug`
        or, without one, in `slug`. `via` and `against_via` label the runs
        with the refs the page reached them by."""
        repo = resolve(slug)
        run = _load_run_or_404(repo, run_id)
        against_run: RunRecord | None = None
        if against is not None:
            against_run = _load_run_or_404(
                resolve(against_slug if against_slug is not None else slug), against
            )
        data = collect_report_data(
            repo,
            run,
            mainline=mainline,
            via=via,
            against=against_run,
            against_via=against_via,
            pr_url_template=pr_url_template,
        )
        if data.history_error is not None:
            _logger.warning(
                "report of run %s has no history, the mainline is unreachable: %s",
                run_id,
                data.history_error,
            )
        try:
            html = render_report(data)
        except (FileNotFoundError, ValueError) as exc:
            # An editable install before `just frontend_build`, not a fault
            # of the request.
            raise HTTPException(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
        filename = (
            f"evaltrack-report-{run_id}.html"
            if against is None
            else f"evaltrack-report-{against}-to-{run_id}.html"
        )
        # An attachment, so the page never runs in the dashboard's origin.
        return Response(
            content=html,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
        if deleted is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, f"run not found: {run_id}")
        # Only the id. A summary needs a run-body read for data the delete makes
        # obsolete.
        return {"id": deleted}

    return router
