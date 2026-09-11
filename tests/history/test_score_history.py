"""Score histories over a mainline history.

Covers the per-(score, case) split, how a run's repeated attempts fold into one
point, the provenance each point carries, and the version segment.
"""

from datetime import UTC, datetime

from evaltrack.core.results import (
    EvaluatorInfo,
    EvaluatorResult,
)
from evaltrack.core.run_record import (
    AttemptOutcome,
    AttemptRecord,
    CaseOutcome,
    CaseRecord,
    MarkerSettings,
    RecordedTest,
    RunRecord,
)
from evaltrack.history.reliability import report_reliability_over
from evaltrack.history.score_history import (
    CaseScoreHistory,
    ScoreHistory,
    report_all_score_history_over,
    report_score_history_over,
)
from evaltrack.history.segments import DEFAULT_WINDOW, HistoryRun

from ..factories import make_eval_run, make_run_id

_SOURCE = EvaluatorInfo(name="Scorer", arguments=None)


def _make_attempt(
    scores: dict[str, float],
    outcome: AttemptOutcome = "passed",
    *,
    bars: dict[str, float] | None = None,
) -> AttemptRecord:
    """`bars` are the runner's own, which reach a stored run on the result and
    never through the marker's settings."""
    bars = bars or {}
    return AttemptRecord(
        outcome=outcome,
        task_duration=0.0,
        results={
            name: EvaluatorResult(
                value=value, runner_bar=bars.get(name), evaluator=_SOURCE
            )
            for name, value in scores.items()
        },
    )


def _make_case(attempts: list[AttemptRecord]) -> CaseRecord:
    """A `CaseRecord` around attempts that carry scores.

    `factories.make_case_result` spells attempts as outcomes alone, which a
    score history cannot use. The rollups are derived the same way it derives
    them, so the case cannot say `passed` over a failing attempt.
    """
    clean = [a for a in attempts if a.outcome != "errored"]
    errors = len(attempts) - len(clean)
    outcome: CaseOutcome
    if errors:
        outcome = "errored"
    elif clean and all(a.outcome == "passed" for a in clean):
        outcome = "passed"
    else:
        outcome = "failed"
    return CaseRecord(
        inputs=None,
        attempts=attempts,
        passed_attempts=sum(a.outcome == "passed" for a in clean),
        clean_attempts=len(clean),
        errored_attempts=errors,
        outcome=outcome,
    )


def _resolve_bars(attempt: AttemptRecord, bars: dict[str, float]) -> AttemptRecord:
    """A recorded run carries the marker's bars on the results they applied to
    as well as in the settings. A helper that writes only the settings does not
    describe a run the plugin produces."""
    return attempt.model_copy(
        update={
            "results": {
                name: EvaluatorResult.model_validate(
                    {**dict(result), "marker_bar": bars[name]}
                )
                if name in bars
                else result
                for name, result in attempt.results.items()
            }
        }
    )


def _make_run(
    label: str,
    cases: dict[str, list[AttemptRecord]],
    *,
    bars: dict[str, float] | None = None,
    eval_version: str | None = None,
) -> RunRecord:
    """A run whose single test `t` recorded the given cases and their attempts."""
    bars = bars or {}
    return make_eval_run(
        run_id=make_run_id(label),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        tests={
            "t": RecordedTest(
                cases={
                    n: _make_case([_resolve_bars(a, bars) for a in attempts])
                    for n, attempts in cases.items()
                },
                marker=MarkerSettings(score_bars=bars, eval_version=eval_version),
            )
        },
    )


def _make_samples(*runs: RunRecord) -> list[HistoryRun]:
    """Mainline samples, newest run first, each annotated with a promote entry."""
    return [
        HistoryRun(
            run,
            f"main-{run.id}",
            datetime(2026, 1, 1, tzinfo=UTC),
            pr=100 + i,
            title=f"PR for {run.id}",
        )
        for i, run in enumerate(runs)
    ]


def _find_score(histories: list[ScoreHistory], score: str) -> ScoreHistory:
    return next(h for h in histories if h.score == score)


def _find_case(
    histories: list[ScoreHistory], score: str, case: str
) -> CaseScoreHistory:
    return _find_score(histories, score).cases[case]


def _only_case(histories: list[ScoreHistory], score: str) -> CaseScoreHistory:
    return next(iter(_find_score(histories, score).cases.values()))


def _collect_values(
    histories: list[ScoreHistory], score: str, case: str
) -> list[float]:
    return [p.value for p in _find_case(histories, score, case).points]


def _collect_charted_runs(
    histories: list[ScoreHistory], score: str, case: str
) -> list[str]:
    return [p.run_id for p in _find_case(histories, score, case).points]


def _collect_pooled_runs(
    samples: list[HistoryRun], case: str, *, window: int = DEFAULT_WINDOW
) -> list[str]:
    """The runs the pass-rate panel pools for the case, oldest first."""
    rel = report_reliability_over(samples, "t", window=window)[case]
    return [p.run_id for p in rel.points]


def test_one_history_per_score_and_case() -> None:
    """The bar is a per-case floor, so each case keeps its own line and no
    average joins them. Two cases and two scores make four histories."""
    runs = [
        _make_run(
            f"r{i}",
            {
                "a": [_make_attempt({"quality": 0.4 + i / 10, "speed": 0.9})],
                "b": [_make_attempt({"quality": 0.8, "speed": 0.5})],
            },
            bars={"quality": 0.5},
        )
        for i in (1, 0)  # newest first
    ]
    histories = report_score_history_over(_make_samples(*runs), "t")

    assert [t.score for t in histories] == ["quality", "speed"]
    assert list(_find_score(histories, "quality").cases) == ["a", "b"]
    assert _collect_values(histories, "quality", "a") == [0.4, 0.5], (
        "points run oldest first"
    )
    assert _collect_values(histories, "speed", "b") == [0.5, 0.5]


def test_points_carry_the_bar_and_the_promote_entry() -> None:
    """A point names the release it came from, so a reader can tie a drop to the
    change that caused it. The commit is the mainline one, not the run's own."""
    histories = report_score_history_over(
        _make_samples(
            _make_run(
                "r0", {"a": [_make_attempt({"quality": 0.7})]}, bars={"quality": 0.5}
            )
        ),
        "t",
    )

    assert _find_case(histories, "quality", "a").bar == 0.5
    point = _only_case(histories, "quality").points[0]
    charted = make_run_id("r0")
    assert (point.commit, point.pr, point.title) == (
        f"main-{charted}",
        100,
        f"PR for {charted}",
    )
    assert point.off_mainline is False, (
        "a promoted run is mainline history, not the viewed run"
    )


def test_a_newest_run_that_never_evaluated_does_not_clear_the_bar() -> None:
    """The bar and version come from the newest run that recorded an eval. A run
    where the test errored before evaluating declares neither, so it must not
    take the chart's bar away."""
    unevaluated = make_eval_run(
        run_id=make_run_id("r1"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        tests={"t": RecordedTest(outcome="errored")},
    )
    evaluated = _make_run(
        "r0",
        {"a": [_make_attempt({"quality": 0.7})]},
        bars={"quality": 0.5},
        eval_version="v1",
    )

    histories = report_score_history_over(_make_samples(unevaluated, evaluated), "t")

    assert _find_case(histories, "quality", "a").bar == 0.5
    assert _find_case(histories, "quality", "a").eval_version == "v1", (
        "the version comes from the same run as the bar"
    )


def test_a_score_with_no_bar_declares_none() -> None:
    histories = report_score_history_over(
        _make_samples(_make_run("r0", {"a": [_make_attempt({"quality": 0.7})]})), "t"
    )

    assert _find_case(histories, "quality", "a").bar is None


def test_a_bar_the_runner_set_is_charted_too() -> None:
    """A runner that bars its own metric gates the score without the marker
    declaring anything, so a chart drawn from the marker's settings alone
    plot a gated score with no line to read it against."""
    histories = report_score_history_over(
        _make_samples(
            _make_run(
                "r0", {"a": [_make_attempt({"quality": 0.7}, bars={"quality": 0.5})]}
            )
        ),
        "t",
    )

    assert _find_case(histories, "quality", "a").bar == 0.5


def test_repeated_attempts_fold_into_one_point_with_its_spread() -> None:
    """Reruns and `repeat` give a run several values for one case. The point is
    their mean, and it carries the spread so a wide run is not read as a move."""
    histories = report_score_history_over(
        _make_samples(
            _make_run(
                "r0",
                {
                    "a": [
                        _make_attempt({"quality": 0.2}),
                        _make_attempt({"quality": 0.8}),
                    ]
                },
            )
        ),
        "t",
    )

    point = _only_case(histories, "quality").points[0]
    assert (point.value, point.low, point.high, point.attempts) == (0.5, 0.2, 0.8, 2), (
        "the point is the mean, carrying the spread and how many attempts made it"
    )


def test_errored_attempts_are_not_observations() -> None:
    """An errored attempt reached no verdict, so whatever it scored says
    nothing about the case. The pass rate uses the same split."""
    histories = report_score_history_over(
        _make_samples(
            _make_run(
                "r0",
                {
                    "a": [
                        _make_attempt({"quality": 0.9}),
                        _make_attempt({"quality": 0.1}, outcome="errored"),
                    ]
                },
            )
        ),
        "t",
    )

    point = _only_case(histories, "quality").points[0]
    assert (point.value, point.attempts) == (0.9, 1), (
        "the errored attempt leaves both the mean and the count alone"
    )


def test_a_run_that_never_scored_the_case_adds_no_point() -> None:
    """A case added later starts its line where it starts, rather than at a
    made-up zero."""
    histories = report_score_history_over(
        _make_samples(
            _make_run(
                "r1", {"a": [_make_attempt({"quality": 0.6})], "b": [_make_attempt({})]}
            ),
            _make_run("r0", {"a": [_make_attempt({"quality": 0.4})]}),
        ),
        "t",
    )

    assert _collect_values(histories, "quality", "a") == [0.4, 0.6]
    assert "b" not in _find_score(histories, "quality").cases, (
        "a case the run never scored gets no line at all"
    )


def test_a_version_bump_ends_the_segment() -> None:
    """A bump says the code under test changed, so the older values measure
    something else. A line through them would draw a movement nobody made."""
    histories = report_score_history_over(
        _make_samples(
            _make_run(
                "r2", {"a": [_make_attempt({"quality": 0.9})]}, eval_version="v2"
            ),
            _make_run(
                "r1", {"a": [_make_attempt({"quality": 0.4})]}, eval_version="v1"
            ),
            _make_run(
                "r0", {"a": [_make_attempt({"quality": 0.3})]}, eval_version="v1"
            ),
        ),
        "t",
    )

    assert _collect_values(histories, "quality", "a") == [0.9]
    assert _find_case(histories, "quality", "a").eval_version == "v2"


def test_a_case_the_newest_run_errored_keeps_the_history_behind_it() -> None:
    """The run that bumped the version reached no verdict for case `a`, so it
    never measured `a` under the new version and cannot put `a`'s older points
    behind a bump. Case `b`, which it did measure, still starts fresh. Otherwise
    the case's chart empties while the pass-rate panel next to it still shows
    the history."""
    samples = _make_samples(
        _make_run(
            "r2",
            {
                "a": [_make_attempt({"quality": 0.9}, outcome="errored")],
                "b": [_make_attempt({"quality": 0.8})],
            },
            eval_version="v2",
        ),
        _make_run(
            "r1",
            {
                "a": [_make_attempt({"quality": 0.5})],
                "b": [_make_attempt({"quality": 0.3})],
            },
            eval_version="v1",
        ),
        _make_run(
            "r0",
            {
                "a": [_make_attempt({"quality": 0.4})],
                "b": [_make_attempt({"quality": 0.2})],
            },
            eval_version="v1",
        ),
    )
    histories = report_score_history_over(samples, "t")

    assert _collect_values(histories, "quality", "a") == [0.4, 0.5]
    assert _collect_values(histories, "quality", "b") == [0.8]
    assert _collect_charted_runs(histories, "quality", "a") == _collect_pooled_runs(
        samples, "a"
    ), "the chart must span the same runs the pass-rate beside it pools"
    assert _collect_charted_runs(histories, "quality", "b") == _collect_pooled_runs(
        samples, "b"
    ), "the measured case starts fresh in both panels alike"


def test_a_case_keeps_the_bar_and_version_of_its_own_segment() -> None:
    """Case `b` ended its segment at the older run, so the older bar is the one
    its points were measured against. Ruling them against the newest run's bar
    would draw a case that cleared 0.5 as sitting under 0.9, and label a v1
    measurement v2. The pass-rate panel beside it already answers per case."""
    samples = _make_samples(
        _make_run(
            "r1",
            {
                "a": [_make_attempt({"quality": 0.95})],
                "b": [_make_attempt({"quality": 0.95}, outcome="errored")],
            },
            bars={"quality": 0.9},
            eval_version="v2",
        ),
        _make_run(
            "r0",
            {
                "a": [_make_attempt({"quality": 0.55})],
                "b": [_make_attempt({"quality": 0.55})],
            },
            bars={"quality": 0.5},
            eval_version="v1",
        ),
    )
    histories = report_score_history_over(samples, "t")

    a, b = _find_case(histories, "quality", "a"), _find_case(histories, "quality", "b")
    assert (a.bar, a.eval_version, _collect_values(histories, "quality", "a")) == (
        0.9,
        "v2",
        [0.95],
    ), "the measured case starts fresh at the bump, under the new bar"
    assert (b.bar, _collect_values(histories, "quality", "b")) == (0.5, [0.55]), (
        "case b's only point cleared 0.5, so it must not be drawn under 0.9"
    )
    assert _find_case(histories, "quality", "b").eval_version == "v1"
    assert _find_case(histories, "quality", "b").eval_version == (
        report_reliability_over(samples, "t")["b"].eval_version
    ), "the chart and the pass-rate beside it must name the same version"


def test_the_window_bounds_the_mainline_points() -> None:
    runs = [
        _make_run(f"r{i}", {"a": [_make_attempt({"quality": i / 10})]})
        for i in (2, 1, 0)
    ]
    histories = report_score_history_over(_make_samples(*runs), "t", window=2)

    assert _collect_values(histories, "quality", "a") == [0.1, 0.2], (
        "the window keeps the two newest"
    )


def test_a_run_that_observed_nothing_does_not_spend_the_case_window() -> None:
    """The window counts the runs that measured the case, so an errored one does
    not push an older point out and leave the chart shorter than the pass-rate
    panel beside it."""
    samples = _make_samples(
        _make_run("r2", {"a": [_make_attempt({"quality": 0.9})]}),
        _make_run("r1", {"a": [_make_attempt({"quality": 0.5}, outcome="errored")]}),
        _make_run("r0", {"a": [_make_attempt({"quality": 0.4})]}),
    )
    histories = report_score_history_over(samples, "t", window=2)

    assert _collect_values(histories, "quality", "a") == [0.4, 0.9]
    assert _collect_charted_runs(histories, "quality", "a") == _collect_pooled_runs(
        samples, "a", window=2
    ), "the chart and the pass-rate must span the same runs"


def test_the_viewed_run_is_the_newest_point_and_does_not_spend_the_window() -> None:
    """The viewed run rides on top of the promoted history. It must not evict a
    mainline point. Otherwise the act of opening a run would change the history
    behind it."""
    mainline = _make_samples(
        _make_run("r1", {"a": [_make_attempt({"quality": 0.6})]}),
        _make_run("r0", {"a": [_make_attempt({"quality": 0.4})]}),
    )
    viewed = _make_run("pr", {"a": [_make_attempt({"quality": 0.2})]})
    samples = [
        HistoryRun(
            viewed, "pr-tip", datetime(2026, 1, 1, tzinfo=UTC), off_mainline=True
        ),
        *mainline,
    ]

    histories = report_score_history_over(samples, "t", window=2)

    assert _collect_values(histories, "quality", "a") == [0.4, 0.6, 0.2]
    points = _only_case(histories, "quality").points
    assert [p.off_mainline for p in points] == [False, False, True]
    assert points[-1].commit == "pr-tip", "the viewed run keeps its own commit"


def test_no_report_for_the_test_gives_nothing() -> None:
    assert report_score_history_over(_make_samples(_make_run("r0", {})), "other") == []


def test_a_test_that_records_no_score_is_omitted() -> None:
    """A pass/fail eval has nothing to plot, so it gets no history rather than an
    empty one a reader has to filter out."""
    assert (
        report_all_score_history_over(
            _make_samples(_make_run("r0", {"a": [_make_attempt({})]}))
        )
        == {}
    )


def test_all_tests_are_projected() -> None:
    scored = {"a": _make_case([_make_attempt({"quality": 0.5})])}
    run = make_eval_run(
        run_id=make_run_id("r0"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        tests={
            name: RecordedTest(cases=scored, marker=MarkerSettings())
            for name in ("t", "u")
        },
    )

    assert set(report_all_score_history_over(_make_samples(run))) == {"t", "u"}
