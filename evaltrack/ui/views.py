"""The cross-run views the dashboard and the report both compute from a
repository: the pooled reliability and score history around one run, and where
the run landed on the mainline. No web framework here, so the report can be
generated without the `[ui]` extra."""

import logging
from concurrent.futures import ThreadPoolExecutor

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
        return load_run_tolerating_another_schema(repo, entry.run_id)

    workers = min(len(entries), _LOAD_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = pool.map(load, entries)
    return [
        HistoryRun(run, entry.commit, entry.moved_at, pr=entry.pr, title=entry.title)
        for entry, run in zip(entries, runs, strict=True)
        if run is not None
    ]


def load_run_history(
    repo: RunRepository,
    *,
    mainline: RunRepository | None,
    run_id: str | None,
    window: int = DEFAULT_WINDOW,
) -> RunHistory:
    """The reliability and score history over `mainline`'s promoted runs, with
    the run `run_id` names in `repo` drawn over them when it is not itself one
    of them. Empty without a mainline, or when it holds no history.

    Raises:
        CorruptRecordError: when the mainline's reflog does not parse.
    """
    history = _load_mainline_history(mainline, window) if mainline else []
    if history and run_id is not None:
        viewed = load_run_tolerating_another_schema(repo, run_id)
        if viewed is not None and all(h.run.id != viewed.id for h in history):
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


def find_mainline_entry(mainline: RunRepository, run_id: str) -> MainlineEntry | None:
    """Where the run landed on the mainline, from the newest `baseline` reflog
    entry pointing at it. None for a run never promoted.

    Raises:
        CorruptRecordError: when the reflog does not parse.
    """
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


def refs_pointing_at(repo: RunRepository, run_id: str) -> list[Ref]:
    """The refs whose tip is `run_id`, by name. A ref whose history cannot be
    read is left out, since its tip is unknown, not absent."""
    refs: list[Ref] = []
    for name in sorted(repo.list_refs()):
        try:
            tip = repo.get_ref(name)
        except (CorruptRecordError, InvalidIdentifierError) as exc:
            _logger.warning("ref %s has an unreadable history: %s", name, exc)
            continue
        if tip is not None and tip.run_id == run_id:
            refs.append(Ref(name=name, tip=tip))
    return refs
