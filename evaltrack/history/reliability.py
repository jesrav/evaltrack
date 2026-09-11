"""Cross-run reliability. Pools a case's per-attempt pass/fail over recent runs
into a pass-rate, and compares it to a `reliability_target`."""

from math import sqrt

from pydantic import AwareDatetime, BaseModel

from evaltrack.history.segments import (
    DEFAULT_WINDOW,
    HistoryRun,
    segment_history_per_case,
)

# 95% two-sided normal quantile
_WILSON_Z = 1.96


def wilson_lower_bound(passes: int, trials: int) -> float:
    """Wilson score lower bound for a binomial proportion, or 0.0 with no
    trials. It stays sensible at small `trials`, unlike `p̂ ± normal`."""
    if trials == 0:
        return 0.0
    p = passes / trials
    n = trials
    z = _WILSON_Z
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - margin) / denom)


class ReliabilityPoint(BaseModel):
    """One run's contribution to a case's reliability history.

    Attributes:
        run_id: The run this point came from.
        created_at: Promote time, or run time off the mainline.
        commit: The mainline commit, or the run's own off the mainline.
        passed_attempts: Passing clean attempts for the case in this run.
        clean_attempts: Attempts at the case that did not error, which is what
            `passed_attempts` is out of.
        attempts: Per-attempt pass/fail of the clean attempts, first attempt first.
        off_mainline: True for a run drawn over the mainline history rather than
            promoted onto it.
    """

    run_id: str
    created_at: AwareDatetime
    commit: str | None = None
    passed_attempts: int
    clean_attempts: int
    attempts: list[bool] = []
    off_mainline: bool = False


class CaseReliability(BaseModel):
    """Pooled reliability for one `(test, case)` over its segment.

    A run off the mainline, the one being viewed drawn over the promoted
    history, is shown and never counted: it is in `points` for the trend and out
    of every number here. Otherwise opening a run nobody promoted would move the
    history it is being compared against.

    Attributes:
        pooled_attempts: Mainline attempts pooled across the segment.
        pooled_passes: Passing attempts among them.
        rate: `pooled_passes / pooled_attempts`, or None when no mainline run
            contributed an attempt.
        lower_bound: Lower bound of the 95% Wilson interval, or None. Unlike
            `rate` it accounts for how many attempts there were.
        target: The `reliability_target` the newest run declared, or None.
        below_target: True only when there is a target and the rate is under it.
        pooled_runs: Mainline runs contributing to the rate.
        eval_version: The `eval_version` the segment shares.
        points: Per-run contributions, oldest first.
        diverged: True when all the case's mainline history sits under another
            `eval_version`. False for a new case, which has no history.
    """

    pooled_attempts: int
    pooled_passes: int
    rate: float | None
    lower_bound: float | None
    target: float | None
    below_target: bool
    pooled_runs: int
    eval_version: str | None
    points: list[ReliabilityPoint]
    diverged: bool = False


def _summarize(
    points: list[ReliabilityPoint],
    *,
    target: float | None,
    eval_version: str | None,
    diverged: bool = False,
) -> CaseReliability:
    """Pool a case's points. An off-mainline point is kept for the trend and
    excluded from the rate."""
    pooled = [p for p in points if not p.off_mainline]
    attempts = sum(p.clean_attempts for p in pooled)
    passes = sum(p.passed_attempts for p in pooled)
    rate = passes / attempts if attempts else None
    lower_bound = wilson_lower_bound(passes, attempts) if attempts else None

    return CaseReliability(
        pooled_attempts=attempts,
        pooled_passes=passes,
        rate=rate,
        lower_bound=lower_bound,
        target=target,
        below_target=target is not None and rate is not None and rate < target,
        pooled_runs=len(pooled),
        eval_version=eval_version,
        points=points,
        diverged=diverged,
    )


def report_reliability_over(
    history: list[HistoryRun],
    nodeid: str,
    *,
    window: int = DEFAULT_WINDOW,
) -> dict[str, CaseReliability]:
    """Pooled reliability for every case in a test's eval, keyed by case id, or
    `{}` when no run recorded an eval for the test.

    Each case is cut by `eval_version`, so a bump starts a fresh history. A run
    with no clean attempt for the case contributes nothing.

    Args:
        history: Newest-first, so the run being viewed, if any, comes before the
            mainline reflog. Not re-sorted here.
        window: How many mainline runs to pool at most.
    """
    by_case, other_version = segment_history_per_case(history, nodeid, window=window)

    result: dict[str, CaseReliability] = {}
    for name, segment in by_case.items():
        newest_settings = segment[0].settings
        points: list[ReliabilityPoint] = []
        for history_run, _meta, case in reversed(segment):
            clean = [a for a in case.attempts if a.outcome != "errored"]
            points.append(
                ReliabilityPoint(
                    run_id=history_run.run.id,
                    created_at=history_run.at,
                    commit=history_run.commit,
                    passed_attempts=sum(1 for a in clean if a.outcome == "passed"),
                    clean_attempts=len(clean),
                    attempts=[a.outcome == "passed" for a in clean],
                    off_mainline=history_run.off_mainline,
                )
            )
        diverged = name in other_version and all(
            e.history_run.off_mainline for e in segment
        )
        result[name] = _summarize(
            points,
            target=newest_settings.reliability_target,
            eval_version=newest_settings.eval_version,
            diverged=diverged,
        )
    return result


def report_all_case_reliability_over(
    history: list[HistoryRun], *, window: int = DEFAULT_WINDOW
) -> dict[str, dict[str, CaseReliability]]:
    """`report_reliability_over` for every test in `history`, keyed by nodeid.
    A test with no segment for any case is omitted."""
    nodeids = {nodeid for run in history for nodeid in run.run.tests}
    result: dict[str, dict[str, CaseReliability]] = {}
    for nodeid in sorted(nodeids):
        rel = report_reliability_over(history, nodeid, window=window)
        if rel:
            result[nodeid] = rel
    return result
