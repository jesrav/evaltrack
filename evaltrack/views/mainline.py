"""What the mainline says about a run: the pooled reliability and score history
around it, where it landed on the mainline, and the `baseline` run to compare
it against."""

import logging
from concurrent.futures import ThreadPoolExecutor

from evaltrack.core.errors import CorruptRecordError, UnsupportedSchemaError
from evaltrack.core.refs import BASELINE_REF, ReflogEntry
from evaltrack.core.run_record import RunRecord
from evaltrack.history.reliability import report_all_case_reliability_over
from evaltrack.history.score_history import report_all_score_history_over
from evaltrack.history.segments import DEFAULT_WINDOW, HistoryRun
from evaltrack.repositories import RunRepository
from evaltrack.views.models import MainlineEntry, RunHistory

_logger = logging.getLogger(__name__)

# Bounded so a pooled view never opens more connections than the store keeps.
_LOAD_WORKERS = 10


def load_readable_run(repo: RunRepository, run_id: str) -> RunRecord | None:
    """`load_run` that returns None, and logs, for a run this evaltrack cannot
    read. That is a run recorded by another evaltrack, or one whose body does
    not parse. One such run must not lose a pooled view."""
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
    except CorruptRecordError as exc:
        _logger.warning("run %s cannot be read: %s", run_id, exc)
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
        return load_readable_run(mainline, entry.run_id)

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
    """The reliability and score history over the promoted runs that `reflog`
    names. `reflog` is the oldest-first `baseline` reflog of `mainline`. `viewed`
    is drawn over those runs when it is not one of them. Empty when there is no
    history."""
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
    mainline: RunRepository,
    *,
    viewed: RunRecord | None,
    window: int = DEFAULT_WINDOW,
) -> RunHistory:
    """`run_history_over` the `baseline` reflog, which this reads from `mainline`.

    Raises:
        CorruptRecordError: when the reflog does not parse.
    """
    reflog = list(mainline.get_reflog(BASELINE_REF))
    return run_history_over(mainline, reflog, viewed=viewed, window=window)


def mainline_entry_in(reflog: list[ReflogEntry], run_id: str) -> MainlineEntry | None:
    """Where the run landed on the mainline. It is taken from the newest entry
    in the oldest-first `baseline` `reflog` that points at the run. None for a
    run never promoted."""
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
    """`mainline_entry_in` the `baseline` reflog, which this reads from `mainline`.

    Raises:
        CorruptRecordError: when the reflog does not parse.
    """
    return mainline_entry_in(list(mainline.get_reflog(BASELINE_REF)), run_id)


def baseline_run_in(
    mainline: RunRepository, reflog: list[ReflogEntry], *, other_than: str
) -> RunRecord | None:
    """The run that the newest entry of the `baseline` `reflog` points at, to
    compare the run `other_than` against. None when the reflog is empty or
    points at that run itself, or when this evaltrack cannot read the run."""
    if not reflog or reflog[-1].run_id == other_than:
        return None
    return load_readable_run(mainline, reflog[-1].run_id)
