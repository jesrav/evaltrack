"""How each numeric score moved from run to run, over the mainline."""

from pydantic import AwareDatetime, BaseModel

from evaltrack.core.run_record import CaseRecord
from evaltrack.history.segments import (
    DEFAULT_WINDOW,
    HistoryRun,
    segment_history_per_case,
)


class ScoreHistoryPoint(BaseModel):
    """One run's value for one case's score.

    Attributes:
        run_id: The run this point came from.
        created_at: Promote time, or run time off the mainline.
        commit: The mainline commit, or the run's own off the mainline.
        pr: The pull request the promote recorded, if it recorded one.
        title: That pull request's title.
        value: The mean score over the case's clean attempts in this run.
        low: The lowest of those scores.
        high: The highest of those scores.
        attempts: How many clean attempts produced this score.
        off_mainline: True when the run is not on the mainline.
    """

    run_id: str
    created_at: AwareDatetime
    commit: str | None = None
    pr: int | None = None
    title: str | None = None
    value: float
    low: float
    high: float
    attempts: int
    off_mainline: bool = False


class CaseScoreHistory(BaseModel):
    """One case's history for one score, over that case's own segment.

    `bar` and `eval_version` belong here rather than on the score, because the
    segment is per case. A case absent or errored in the newest run
    ends its history at an older run. That run's bar and version are the ones
    its points were measured against.

    Attributes:
        bar: The bar the newest run in this case's history judged the score
            against, or None.
        eval_version: The version label that history shares.
        points: The case's values, oldest first.
    """

    bar: float | None = None
    eval_version: str | None = None
    points: list[ScoreHistoryPoint]


class ScoreHistory(BaseModel):
    """One score across the cases of a test's eval, each over its own history.

    Attributes:
        score: The name of the score.
        cases: One history per case that recorded this score, keyed by case id.
    """

    score: str
    cases: dict[str, CaseScoreHistory]


def _score_values(case: CaseRecord) -> dict[str, list[float]]:
    """Each numeric score the case's clean attempts produced, keyed by score name."""
    values: dict[str, list[float]] = {}
    for attempt in case.attempts:
        if attempt.outcome == "errored":
            continue
        for name, result in attempt.results.items():
            if result.is_score:
                values.setdefault(name, []).append(float(result.value))
    return values


def _recorded_bar(case: CaseRecord, score_name: str) -> float | None:
    """The bar the score was judged against, off the first attempt that recorded
    it. One run judges every attempt of a case against the same bar."""
    for attempt in case.attempts:
        result = attempt.results.get(score_name)
        if result is not None:
            return result.bar
    return None


def _make_point(history_run: HistoryRun, values: list[float]) -> ScoreHistoryPoint:
    return ScoreHistoryPoint(
        run_id=history_run.run.id,
        created_at=history_run.at,
        commit=history_run.commit,
        pr=history_run.pr,
        title=history_run.title,
        value=sum(values) / len(values),
        low=min(values),
        high=max(values),
        attempts=len(values),
        off_mainline=history_run.off_mainline,
    )


def report_score_history_over(
    history: list[HistoryRun],
    nodeid: str,
    *,
    window: int = DEFAULT_WINDOW,
) -> list[ScoreHistory]:
    """Every numeric score of a test's eval, one history per score and case, with
    points oldest first. Empty when no run recorded an eval, or none recorded a
    score.

    A history per case rather than a mean per score, because a bar is a floor
    each case has to clear on its own. Scores and cases keep the order the
    newest run recorded them in.

    Args:
        history: Newest-first, so the run being viewed, if any, comes before the
            mainline reflog. Not re-sorted here.
        window: How many mainline runs a case's segment can hold.
    """
    by_case = segment_history_per_case(history, nodeid, window=window).by_case
    # Registered newest-first, so the histories come out in the newest run's order.
    by_score: dict[str, dict[str, list[ScoreHistoryPoint]]] = {}
    for case_id, segment in by_case.items():
        for entry in segment:
            for score_name in _score_values(entry.case):
                by_score.setdefault(score_name, {}).setdefault(case_id, [])
    for case_id, segment in by_case.items():
        for entry in reversed(segment):
            for score_name, values in _score_values(entry.case).items():
                by_score[score_name][case_id].append(
                    _make_point(entry.history_run, values)
                )
    # The newest entry of a case's own segment, which is what its bar and
    # version come from. A run that recorded no marker is skipped, so every
    # case has one.
    newest = {name: entries[0] for name, entries in by_case.items()}
    return [
        ScoreHistory(
            score=score_name,
            cases={
                case_id: CaseScoreHistory(
                    # Off the result rather than the settings, so a bar the
                    # runner set is charted as well as gated on.
                    bar=_recorded_bar(newest[case_id].case, score_name),
                    eval_version=newest[case_id].settings.eval_version,
                    points=points,
                )
                for case_id, points in cases.items()
            },
        )
        for score_name, cases in by_score.items()
    ]


def report_all_score_history_over(
    history: list[HistoryRun], *, window: int = DEFAULT_WINDOW
) -> dict[str, list[ScoreHistory]]:
    """`report_score_history_over` for every test in `history`, keyed by nodeid.
    A test that records no score is omitted."""
    nodeids = {nodeid for run in history for nodeid in run.run.tests}
    result: dict[str, list[ScoreHistory]] = {}
    for nodeid in sorted(nodeids):
        histories = report_score_history_over(history, nodeid, window=window)
        if histories:
            result[nodeid] = histories
    return result
