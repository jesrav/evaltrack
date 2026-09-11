"""A case's runs over the mainline, cut into the segment that shares one
`eval_version`. Holds what a run contributes to that history, and how the
segment is found."""

from datetime import datetime
from typing import NamedTuple

from evaltrack.core.run_record import CaseRecord, MarkerSettings, RunRecord


class HistoryRun(NamedTuple):
    """One run and its place in the history.

    `commit` and `at` place the run, as the caller supplies them. A run off the
    mainline uses its own. `pr` and `title` name the pull request the promote
    recorded, when it recorded one.
    """

    run: RunRecord
    commit: str | None
    at: datetime
    off_mainline: bool = False
    pr: int | None = None
    title: str | None = None


# How many mainline runs a segment holds at most (a rolling window).
DEFAULT_WINDOW = 50


def _has_clean(case: CaseRecord) -> bool:
    return any(a.outcome != "errored" for a in case.attempts)


class HistoryEntry(NamedTuple):
    """One run's record of a case. `history_run` is the run and its place in the
    history, `settings` what the marker asked for, and `case` the case as that
    run recorded it."""

    history_run: HistoryRun
    settings: MarkerSettings
    case: CaseRecord


HistorySegment = list[HistoryEntry]
"""A case's history back to the last `eval_version` change, newest first, and
no longer than the window. Only these runs may be compared with one another.

Not every run at the newest version: the first run at another version closes
the history for good, so a version that was reverted to does not reopen the
stretch that ran under it before.
"""


class CaseSegments(NamedTuple):
    """The segment of each case in a test's eval, keyed by case id in
    first-seen order. `other_version` names the cases whose history ended at a
    mainline run with another `eval_version`. That is what tells a version bump
    from a new case."""

    by_case: dict[str, HistorySegment]
    other_version: set[str]


def segment_history_per_case(
    history: list[HistoryRun], nodeid: str, *, window: int
) -> CaseSegments:
    """Split each case of a test's eval into the segment it may be compared over.

    A segment is the newest-first stretch of runs sharing the `eval_version`
    of the newest run with a clean attempt for the case. It ends at the first
    run with another version, or once it holds `window` mainline runs. A run
    with no clean attempt for the case is skipped. A run off the mainline is
    included but does not count toward the window.

    Args:
        history: Newest-first. Not re-sorted here.
        window: How many mainline runs a case's segment can hold.
    """
    by_case: dict[str, HistorySegment] = {}
    eval_version: dict[str, str | None] = {}
    mainline: dict[str, int] = {}
    closed: set[str] = set()
    other_version: set[str] = set()
    for history_run in history:
        recorded = history_run.run.tests.get(nodeid)
        if recorded is None or recorded.marker is None:
            continue
        for name, case in recorded.cases.items():
            if name in closed or not _has_clean(case):
                continue
            if name not in by_case:
                by_case[name] = []
                eval_version[name] = recorded.marker.eval_version
            elif recorded.marker.eval_version != eval_version[name]:
                closed.add(name)
                if not history_run.off_mainline:
                    other_version.add(name)
                continue
            by_case[name].append(HistoryEntry(history_run, recorded.marker, case))
            if history_run.off_mainline:
                continue
            mainline[name] = mainline.get(name, 0) + 1
            if mainline[name] >= window:
                closed.add(name)
    return CaseSegments(by_case, other_version)
