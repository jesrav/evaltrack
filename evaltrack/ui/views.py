"""The cross-run views the dashboard and the report both compute from a
repository: the pooled reliability and score history around one run, and where
the run landed on the mainline. No web framework here, so the report can be
generated without the `[ui]` extra."""

import logging
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from evaltrack.core.errors import (
    CorruptRecordError,
    InvalidIdentifierError,
    UnsupportedSchemaError,
)
from evaltrack.core.refs import BASELINE_REF, Ref, ReflogEntry
from evaltrack.core.run_record import RunRecord
from evaltrack.history.reliability import report_all_case_reliability_over
from evaltrack.history.score_history import report_all_score_history_over
from evaltrack.history.segments import DEFAULT_WINDOW, HistoryRun
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import MainlineEntry, RunHistory

_logger = logging.getLogger(__name__)

# Bounded so a pooled view never opens more connections than the store keeps.
_LOAD_WORKERS = 10


def load_run_tolerating_another_schema(
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


class RefTip(NamedTuple):
    """A ref and its tip. `tip` is None with `error` set when the ref's history
    cannot be read, and None alone when the ref points at nothing."""

    name: str
    tip: ReflogEntry | None
    error: str | None


def iter_ref_tips(repo: RunRepository) -> Iterator[RefTip]:
    """Every ref by name with its tip. A ref whose history cannot be read is
    still yielded, logged, so a caller can show or repair it."""
    for name in sorted(repo.list_refs()):
        try:
            yield RefTip(name, repo.get_ref(name), None)
        except (CorruptRecordError, InvalidIdentifierError) as exc:
            _logger.warning("ref %s has an unreadable history: %s", name, exc)
            yield RefTip(name, None, str(exc))


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


def _load_mainline_history(
    mainline: RunRepository, reflog: list[ReflogEntry], *, window: int
) -> list[HistoryRun]:
    """The newest `window` runs on the oldest-first `baseline` reflog, newest-first,
    one per run.

    Each carries the promote-time commit, not the run's eval-time commit. A run
    promoted more than once is taken from its newest entry.
    """
    entries = _newest_entry_per_run(reflog[-window:][::-1]) if window > 0 else []
    if not entries:
        return []

    # Each load is one store read, so they overlap well.
    def load(entry: ReflogEntry) -> RunRecord | None:
        return load_run_tolerating_another_schema(mainline, entry.run_id)

    workers = min(len(entries), _LOAD_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = pool.map(load, entries)
    return [
        HistoryRun(run, entry.commit, entry.moved_at, pr=entry.pr, title=entry.title)
        for entry, run in zip(entries, runs, strict=True)
        if run is not None
    ]


def run_history_over(
    mainline: RunRepository,
    reflog: list[ReflogEntry],
    *,
    viewed: RunRecord | None,
    window: int = DEFAULT_WINDOW,
) -> RunHistory:
    """The reliability and score history over the runs `reflog`, `mainline`'s
    oldest-first `baseline` history, promotes, with `viewed` drawn over them
    when it is not itself one of them. Empty when there is no history."""
    history = _load_mainline_history(mainline, reflog, window=window)
    if history and viewed is not None and all(h.run.id != viewed.id for h in history):
        history = [
            HistoryRun(viewed, viewed.commit, viewed.created_at, off_mainline=True),
            *history,
        ]
    if not history:
        return RunHistory()
    return RunHistory(
        reliability=report_all_case_reliability_over(history, window=window),
        score_history=report_all_score_history_over(history, window=window),
    )


def load_run_history(
    mainline: RunRepository | None,
    *,
    viewed: RunRecord | None,
    window: int = DEFAULT_WINDOW,
) -> RunHistory:
    """The reliability and score history over `mainline`'s promoted runs, with
    `viewed` drawn over them when it is not itself one of them. Empty without
    a mainline, or when it holds no history.

    Raises:
        CorruptRecordError: when the mainline's reflog does not parse.
    """
    if mainline is None:
        return RunHistory()
    reflog = list(mainline.get_reflog(BASELINE_REF))
    return run_history_over(mainline, reflog, viewed=viewed, window=window)


def mainline_entry_in(
    reflog: Iterable[ReflogEntry], run_id: str
) -> MainlineEntry | None:
    """Where the run landed on the mainline, from the newest entry of the
    oldest-first `baseline` `reflog` pointing at it. None for a run never
    promoted."""
    match: ReflogEntry | None = None
    for entry in reflog:
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


def find_mainline_entry(mainline: RunRepository, run_id: str) -> MainlineEntry | None:
    """`mainline_entry_in` over `mainline`'s own `baseline` reflog.

    Raises:
        CorruptRecordError: when the reflog does not parse.
    """
    return mainline_entry_in(mainline.get_reflog(BASELINE_REF), run_id)


def refs_pointing_at(repo: RunRepository, run_id: str) -> list[Ref]:
    """The refs whose tip is `run_id`, by name. A ref whose history cannot be
    read is left out, since its tip is unknown, not absent."""
    return [
        Ref(name=name, tip=tip)
        for name, tip, _ in iter_ref_tips(repo)
        if tip is not None and tip.run_id == run_id
    ]
