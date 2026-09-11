"""The cross-run view: how reliably each case passes, and how its scores move."""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter

from evaltrack.core.errors import UnsupportedSchemaError
from evaltrack.core.refs import BASELINE_REF, ReflogEntry
from evaltrack.core.run_record import RunRecord
from evaltrack.history.reliability import report_all_case_reliability_over
from evaltrack.history.score_history import report_all_score_history_over
from evaltrack.history.segments import DEFAULT_WINDOW, HistoryRun
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import RunHistory

_logger = logging.getLogger(__name__)

# Bounded so a pooled view never opens more connections than the store keeps.
_LOAD_WORKERS = 10


def _load_run_tolerating_another_schema(
    repo: RunRepository, run_id: str
) -> RunRecord | None:
    """`load_run` that logs a run recorded by another evaltrack and returns None,
    so one such run does not lose a pooled view."""
    try:
        return repo.load_run(run_id)
    except UnsupportedSchemaError as exc:
        _logger.warning(
            "run %s is intact but was recorded by another evaltrack, and "
            "reading it needs that version: %s",
            run_id,
            exc,
        )
        return None


def _newest_entry_per_run(entries: list[ReflogEntry]) -> list[ReflogEntry]:
    """Keep one entry per run in a newest-first reflog tail, the newest of each."""
    seen: set[str] = set()
    unique: list[ReflogEntry] = []
    for entry in entries:
        if entry.run_id in seen:
            continue
        seen.add(entry.run_id)
        unique.append(entry)
    return unique


def _load_mainline_history(repo: RunRepository, window: int) -> list[HistoryRun]:
    """The runs on the `baseline` reflog, newest-first, one per run.

    Each carries the promote-time commit, not the run's eval-time commit. A run
    promoted more than once is taken from its newest entry.
    """
    # Read strictly, because a pass rate over dropped entries is worse than no
    # number.
    entries = _newest_entry_per_run(repo.tail_reflog(BASELINE_REF, window))
    if not entries:
        return []

    # Each load is one store read, so they overlap well.
    def load(entry: ReflogEntry) -> RunRecord | None:
        return _load_run_tolerating_another_schema(repo, entry.run_id)

    workers = min(len(entries), _LOAD_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = pool.map(load, entries)
    return [
        HistoryRun(run, entry.commit, entry.moved_at, pr=entry.pr, title=entry.title)
        for entry, run in zip(entries, runs, strict=True)
        if run is not None
    ]


def build_history_router(
    resolve: Callable[[str], RunRepository], mainline: RunRepository | None
) -> APIRouter:
    """`mainline` is the repository the promote history lives in, None when no
    mount supplies one. Without it there is nothing to measure over."""
    router = APIRouter(prefix="/api/repositories/{slug}")

    def load_history_for_run(
        slug: str, *, run_id: str | None, window: int
    ) -> list[HistoryRun]:
        """The mainline history with the viewed run drawn over it, empty without a
        mainline repository."""
        repo = resolve(slug)
        history = _load_mainline_history(mainline, window) if mainline else []
        if not history or run_id is None:
            return history
        viewed = _load_run_tolerating_another_schema(repo, run_id)
        if viewed is None or any(h.run.id == viewed.id for h in history):
            return history
        return [
            HistoryRun(viewed, viewed.commit, viewed.created_at, off_mainline=True),
            *history,
        ]

    @router.get("/history")
    def get_history(  # pyright: ignore[reportUnusedFunction]
        slug: str, run_id: str | None = None
    ) -> RunHistory:
        # `run_id` draws the viewed run over the history without folding it
        # into the rate.
        history = load_history_for_run(slug, run_id=run_id, window=DEFAULT_WINDOW)
        if not history:
            return RunHistory()
        return RunHistory(
            reliability=report_all_case_reliability_over(
                history, window=DEFAULT_WINDOW
            ),
            score_history=report_all_score_history_over(history, window=DEFAULT_WINDOW),
        )

    return router
