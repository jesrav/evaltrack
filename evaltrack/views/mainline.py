"""What the mainline says about a run: the pooled reliability and score history
around it, where it landed on the mainline, and the `baseline` run to compare
it against. The mainline is the history of the `baseline` ref on the remote."""

import logging
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum

from evaltrack.core.errors import CorruptRecordError, UnsupportedSchemaError
from evaltrack.core.refs import BASELINE_REF, ReflogEntry
from evaltrack.core.run_record import RunRecord
from evaltrack.history.reliability import report_all_case_reliability_over
from evaltrack.history.score_history import report_all_score_history_over
from evaltrack.history.segments import DEFAULT_WINDOW, HistoryRun
from evaltrack.repositories import RunRepository
from evaltrack.views.models import MainlineEntry, RunHistory

_logger = logging.getLogger(__name__)


class NoMainline(StrEnum):
    """Why there is no mainline to read. A reason is shown to a reader, and a
    page that shows it can be handed around, so each is fixed words, never a
    host, a path or an error's own text."""

    NO_REMOTE = "no remote is configured"
    REMOTE_DID_NOT_OPEN = "the remote did not open"
    REMOTE_NOT_REACHED = "the remote was not reached"
    REFLOG_DID_NOT_PARSE = "the baseline reflog did not parse"


# Bounded so a pooled view never opens more connections than the store keeps.
_LOAD_WORKERS = 10


def load_readable_run(repo: RunRepository, run_id: str) -> RunRecord | None:
    """Load a run, or return None when this evaltrack cannot read it. That could be a
    run recorded by a newer evaltrack, or one whose body does not parse. The
    failure is logged. One bad run must not cost a whole history."""
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
    remote: RunRepository, reflog: list[ReflogEntry], *, window: int
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
        return load_readable_run(remote, entry.run_id)

    workers = min(len(entries), _LOAD_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = pool.map(load, entries)
    return [
        HistoryRun(run, entry.commit, entry.moved_at, pr=entry.pr, title=entry.title)
        for entry, run in zip(entries, runs, strict=True)
        if run is not None
    ]


def run_history_over(
    remote: RunRepository,
    reflog: list[ReflogEntry],
    *,
    viewed_run: RunRecord | None,
    window: int = DEFAULT_WINDOW,
) -> RunHistory:
    """The RunHistory (reliability and score history) over the runs on the
    mainline. `reflog` is the oldest-first `baseline` reflog of `remote`. Each
    of its entries names a promoted run, and gives the commit and PR that promoted
    it, which label the history's points. `viewed_run` is the run on screen. When
    it is not one of the mainline runs, it is added as one more point, so it shows
    next to them without counting in the rates. Empty when there is no history."""
    history = _load_mainline_history(remote, reflog, window=window)
    if (
        history
        and viewed_run is not None
        and all(h.run.id != viewed_run.id for h in history)
    ):
        history = [
            HistoryRun(
                viewed_run, viewed_run.commit, viewed_run.created_at, off_mainline=True
            ),
            *history,
        ]
    if not history:
        return RunHistory()
    return RunHistory(
        reliability=report_all_case_reliability_over(history, window=window),
        score_history=report_all_score_history_over(history, window=window),
    )


def load_run_history(
    remote: RunRepository,
    *,
    viewed_run: RunRecord | None,
    window: int = DEFAULT_WINDOW,
) -> RunHistory:
    """`run_history_over` the `baseline` reflog, which this reads from `remote`.

    Raises:
        CorruptRecordError: when the reflog does not parse.
    """
    reflog = list(remote.get_reflog(BASELINE_REF))
    return run_history_over(remote, reflog, viewed_run=viewed_run, window=window)


def mainline_entry_for(reflog: list[ReflogEntry], run_id: str) -> MainlineEntry | None:
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


def load_mainline_entry(remote: RunRepository, run_id: str) -> MainlineEntry | None:
    """`mainline_entry_for` the `baseline` reflog, which this reads from `remote`.

    Raises:
        CorruptRecordError: when the reflog does not parse.
    """
    return mainline_entry_for(list(remote.get_reflog(BASELINE_REF)), run_id)


def baseline_run_for(
    remote: RunRepository, reflog: list[ReflogEntry], *, run_id: str
) -> RunRecord | None:
    """The baseline run to compare the run `run_id` against. It is the run that the
    newest entry of the `baseline` `reflog` points at. None when the reflog is
    empty, when it points at that run itself, or when this evaltrack cannot read
    the run."""
    if not reflog or reflog[-1].run_id == run_id:
        return None
    return load_readable_run(remote, reflog[-1].run_id)
