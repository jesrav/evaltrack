"""Cross-run reliability.

Covers the per-case segmented pass rate, the first-versus-later diagnostic,
the exclusion of errored attempts, and the Wilson lower bound reported beside
the pooled rate.
"""

from datetime import UTC, datetime

import pytest

from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_record import (
    MarkerSettings,
    RecordedTest,
    RunRecord,
)
from evaltrack.history.reliability import (
    CaseReliability,
    report_all_case_reliability_over,
    report_reliability_over,
    wilson_lower_bound,
)
from evaltrack.history.segments import DEFAULT_WINDOW, HistoryRun

from ..factories import (
    make_case_result,
    make_case_run,
    make_crash_round,
    make_eval_run,
    make_repeat_round,
    make_run_id,
)


def build_samples_from_runs(runs: list[RunRecord]) -> list[HistoryRun]:
    """Newest-first samples from a plain run list, each annotated with its own
    eval-time commit/timestamp (mainline only, no viewed run)."""
    ordered = sorted(runs, key=lambda r: (r.created_at, r.id), reverse=True)
    return [HistoryRun(r, r.commit, r.created_at) for r in ordered]


def pool_report_reliability(
    runs: list[RunRecord],
    nodeid: str,
    *,
    window: int = DEFAULT_WINDOW,
) -> dict[str, CaseReliability]:
    """`report_reliability_over` for a plain (recency-ordered) run list."""
    return report_reliability_over(build_samples_from_runs(runs), nodeid, window=window)


def pool_case_reliability(
    runs: list[RunRecord],
    nodeid: str,
    case_id: str,
    *,
    window: int = DEFAULT_WINDOW,
) -> CaseReliability | None:
    """Pooled reliability for a single case, or None when absent."""
    return pool_report_reliability(runs, nodeid, window=window).get(case_id)


def _make_run(
    label: str,
    created_at: str,
    attempts: str,
    *,
    inputs: object = None,
    eval_version: str | None = None,
    target: float | None = None,
    commit: str | None = None,
    case: str = "c",
) -> RunRecord:
    """A run whose single test `t` recorded one case with the given attempts."""
    return make_case_run(
        label,
        created_at,
        attempts,
        case=case,
        inputs=inputs,
        eval_version=eval_version,
        reliability_target=target,
        commit=commit,
    )


def _make_two_case_run(
    label: str,
    created_at: str,
    *,
    a: str,
    b: str,
    eval_version: str | None = None,
) -> RunRecord:
    """A run whose test `t` recorded two cases, so a test-level effect
    (pooling, an `eval_version` bump) applies to both at once."""
    return make_eval_run(
        run_id=make_run_id(label),
        created_at=created_at,
        tests={
            "t": RecordedTest(
                cases={"a": make_case_result(a), "b": make_case_result(b)},
                marker=MarkerSettings(eval_version=eval_version),
            )
        },
    )


# pass-rate: pooling


def test_pools_all_attempts_across_runs() -> None:
    runs = [
        _make_run("A", "2026-01-01T00:00:00Z", "PPPFF"),
        _make_run("B", "2026-01-02T00:00:00Z", "PPPPF"),
    ]
    rel = pool_case_reliability(runs, "t", "c")
    assert rel is not None
    assert rel.pooled_attempts == 10
    assert rel.pooled_passes == 7
    assert rel.rate == 0.7
    assert rel.pooled_runs == 2


def test_returns_none_when_case_absent() -> None:
    runs = [_make_run("A", "2026-01-01T00:00:00Z", "P")]
    assert pool_case_reliability(runs, "t", "missing") is None


def test_below_target_flag() -> None:
    runs = [
        _make_run("A", "2026-01-01T00:00:00Z", "PPPPPPFFFF", target=0.9),
    ]
    rel = pool_case_reliability(runs, "t", "c")
    assert rel is not None
    assert rel.rate == 0.6
    assert rel.target == 0.9
    assert rel.below_target is True


# lower_bound: Wilson confidence floor that accounts for sample size


def test_lower_bound_is_none_when_no_samples() -> None:
    """A brand-new case with only a viewed run (no mainline runs) has no
    mainline pass-rate to bound, so `lower_bound` is None alongside `rate`."""
    viewed = _make_run("A", "2026-01-01T00:00:00Z", "P")
    samples = [HistoryRun(viewed, viewed.commit, viewed.created_at, off_mainline=True)]
    rel = report_reliability_over(samples, "t")["c"]
    assert rel.pooled_attempts == 0
    assert rel.rate is None
    assert rel.lower_bound is None


def test_lower_bound_reflects_sample_size() -> None:
    """7 of 10 and 700 of 1000 give the same `rate` of 0.7 but a very different
    `lower_bound`. The smaller sample has a much wider confidence interval, so
    its floor is lower. `rate` alone cannot tell the two apart."""
    small = [_make_run("SM1", "2026-01-01T00:00:00Z", "PPPPPPPFFF")]
    large = [
        _make_run(
            f"{i:03d}", f"2026-02-01T{i // 60:02d}:{i % 60:02d}:00Z", "PPPPPPPFFF"
        )
        for i in range(100)
    ]
    rel_small = pool_case_reliability(small, "t", "c")
    rel_large = pool_case_reliability(large, "t", "c", window=100)
    assert rel_small is not None
    assert rel_large is not None
    assert rel_small.rate == rel_large.rate == 0.7
    assert rel_small.lower_bound is not None
    assert rel_large.lower_bound is not None
    assert rel_small.lower_bound < rel_large.lower_bound, (
        "fewer samples widen the interval, lowering the floor"
    )


def test_lower_bound_is_zero_when_all_fail() -> None:
    """With 0 passes out of N samples, the Wilson lower bound floors at 0.0,
    not None. There are samples, and they all failed."""
    runs = [_make_run("A", "2026-01-01T00:00:00Z", "FFFFF")]
    rel = pool_case_reliability(runs, "t", "c")
    assert rel is not None
    assert rel.pooled_attempts == 5
    assert rel.rate == 0.0
    assert rel.lower_bound == 0.0


def test_report_reliability_pools_every_case_in_one_pass() -> None:
    runs = [
        _make_two_case_run("A", "2026-01-01T00:00:00Z", a="P", b="F"),
        _make_two_case_run("B", "2026-01-02T00:00:00Z", a="P", b="P"),
    ]
    rels = pool_report_reliability(runs, "t")
    assert set(rels) == {"a", "b"}
    assert rels["a"].rate == 1.0  # 2/2
    assert rels["b"].rate == 0.5  # 1/2


# per-case segmentation


def test_input_change_without_version_bump_pools() -> None:
    """Segmentation is by `eval_version` alone, so a changed input with no `eval_version`
    bump pools with the older runs. That is the cost of one manual rule. A
    forgotten bump pools runs that cannot be compared."""
    runs = [
        _make_run("C", "2026-01-03T00:00:00Z", "P", inputs="new"),
        _make_run("B", "2026-01-02T00:00:00Z", "F", inputs="old"),
        _make_run("A", "2026-01-01T00:00:00Z", "F", inputs="old"),
    ]
    rel = pool_case_reliability(runs, "t", "c")
    assert rel is not None
    assert rel.pooled_runs == 3, "all pooled: no version change to split them"
    assert rel.rate == 1 / 3


def test_segment_breaks_on_version_change() -> None:
    runs = [
        _make_run("B", "2026-01-02T00:00:00Z", "P", eval_version="v2"),
        _make_run("A", "2026-01-01T00:00:00Z", "F", eval_version="v1"),
    ]
    rel = pool_case_reliability(runs, "t", "c")
    assert rel is not None
    assert rel.pooled_runs == 1
    assert rel.eval_version == "v2"


def test_version_bump_segments_every_case_in_the_report() -> None:
    # `eval_version` is test-level, so a bump starts a fresh segment for every
    # case under that test at once.
    runs = [
        _make_two_case_run(
            "B", "2026-01-02T00:00:00Z", a="P", b="P", eval_version="v2"
        ),
        _make_two_case_run(
            "A", "2026-01-01T00:00:00Z", a="P", b="P", eval_version="v1"
        ),
    ]
    rels = pool_report_reliability(runs, "t")
    assert rels["a"].pooled_runs == 1
    assert rels["b"].pooled_runs == 1, "the shared bump segments case b too"


# diverged: mainline history exists but sits under another version


def _history_with_viewed_run(
    viewed: RunRecord, mainline: list[RunRecord]
) -> list[HistoryRun]:
    """Newest-first history with `viewed` drawn over `mainline` (newest first)."""
    return [
        HistoryRun(viewed, viewed.commit, viewed.created_at, off_mainline=True),
        *[HistoryRun(r, r.commit, r.created_at) for r in mainline],
    ]


def test_version_bump_leaves_the_case_without_a_segment() -> None:
    """Nothing pools across the bump, so the trend holds the viewed run alone.
    That fact is reported, so a reader can tell an empty trend apart from a case
    mainline never ran."""
    viewed = _make_run("C", "2026-01-03T00:00:00Z", "P", eval_version="v2")
    mainline = [
        _make_run("B", "2026-01-02T00:00:00Z", "P", eval_version="v1"),
        _make_run("A", "2026-01-01T00:00:00Z", "F", eval_version="v1"),
    ]
    rel = report_reliability_over(_history_with_viewed_run(viewed, mainline), "t")["c"]
    assert rel.diverged is True
    assert rel.pooled_attempts == 0
    assert [p.off_mainline for p in rel.points] == [True], (
        "only the viewed run is charted"
    )


def test_a_new_case_is_not_diverged() -> None:
    """A case with no mainline history has nothing to compare against, so its
    empty trend is correct rather than a result of the version rule."""
    viewed = _make_run("B", "2026-01-02T00:00:00Z", "P", eval_version="v1")
    mainline = [
        _make_run("A", "2026-01-01T00:00:00Z", "P", eval_version="v1", case="other"),
    ]
    rel = report_reliability_over(_history_with_viewed_run(viewed, mainline), "t")["c"]
    assert rel.diverged is False
    assert rel.pooled_attempts == 0


def test_case_missing_from_the_newest_mainline_run_still_pools_its_history() -> None:
    """The newest mainline run carries a new version but no observation of this
    case, so the case's own history is still all at the viewed run's version. It
    has a real rate, and a report of divergence would hide it."""
    viewed = _make_run("D", "2026-01-04T00:00:00Z", "P", eval_version="v1")
    mainline = [
        _make_run("C", "2026-01-03T00:00:00Z", "P", eval_version="v2", case="other"),
        _make_run("B", "2026-01-02T00:00:00Z", "P", eval_version="v1"),
        _make_run("A", "2026-01-01T00:00:00Z", "F", eval_version="v1"),
    ]
    rel = report_reliability_over(_history_with_viewed_run(viewed, mainline), "t")["c"]
    assert rel.diverged is False
    assert rel.pooled_attempts == 2
    assert rel.rate == 0.5


def test_case_errored_in_the_newest_mainline_run_still_pools_its_history() -> None:
    """Same as a missing case, but the observation is lost to an errored attempt.
    An error is not a version change, so the older history stays comparable."""
    viewed = _make_run("D", "2026-01-04T00:00:00Z", "P", eval_version="v1")
    mainline = [
        _make_run("C", "2026-01-03T00:00:00Z", "E", eval_version="v2"),
        _make_run("B", "2026-01-02T00:00:00Z", "P", eval_version="v1"),
        _make_run("A", "2026-01-01T00:00:00Z", "F", eval_version="v1"),
    ]
    rel = report_reliability_over(_history_with_viewed_run(viewed, mainline), "t")["c"]
    assert rel.diverged is False
    assert rel.pooled_attempts == 2
    assert rel.rate == 0.5


def test_matching_versions_are_not_diverged() -> None:
    viewed = _make_run("B", "2026-01-02T00:00:00Z", "P", eval_version="v1")
    mainline = [
        _make_run("A", "2026-01-01T00:00:00Z", "P", eval_version="v1"),
    ]
    rel = report_reliability_over(_history_with_viewed_run(viewed, mainline), "t")["c"]
    assert rel.diverged is False
    assert rel.pooled_attempts == 1


def test_mainline_only_view_is_never_diverged() -> None:
    """Without a viewed run there is nothing to compare, so a version bump in
    the history segments the pool."""
    runs = [
        _make_run("B", "2026-01-02T00:00:00Z", "P", eval_version="v2"),
        _make_run("A", "2026-01-01T00:00:00Z", "P", eval_version="v1"),
    ]
    rel = pool_report_reliability(runs, "t")["c"]
    assert rel.diverged is False
    assert rel.pooled_attempts == 1


# errored attempts excluded from the pass-rate


def test_errored_attempts_excluded_from_rate() -> None:
    # 2 clean attempts (1 pass, 1 fail) and 2 errored, so the rate is 1/2,
    # not 1/4.
    runs = [_make_run("A", "2026-01-01T00:00:00Z", "PFEE")]
    rel = pool_case_reliability(runs, "t", "c")
    assert rel is not None
    assert rel.pooled_attempts == 2
    assert rel.pooled_passes == 1
    assert rel.rate == 0.5


def test_crashed_task_attempt_stays_out_of_the_pool() -> None:
    """A crashed task is recorded as an errored attempt, which reaches no
    verdict. It is not a sample of the pass rate, so the pool must skip it. See
    docs/flakiness.md, "Cross-run reliability". This run is built through the
    recorder, so a change there cannot feed crashes into the pool
    unnoticed."""
    rec = EvalRecorder(
        run_id=make_run_id("A"), created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    rec.add_round("t", make_repeat_round([True, True]))
    rec.add_round("t", make_crash_round(case_id="c"), continuing=True)
    run = rec.to_run_record()

    test = run.tests["t"]
    assert test.cases["c"].clean_attempts == 2, "the crash is not a clean attempt"
    assert test.cases["c"].errored_attempts == 1

    rel = pool_case_reliability([run], "t", "c")
    assert rel is not None
    assert rel.pooled_attempts == 2, "the crashed attempt contributes no sample"
    assert rel.pooled_passes == 2
    assert rel.rate == 1.0


def test_all_errored_run_contributes_no_sample() -> None:
    runs = [
        _make_run("A", "2026-01-01T00:00:00Z", "P"),
        _make_run("B", "2026-01-02T00:00:00Z", "EEE"),
    ]
    rel = pool_case_reliability(runs, "t", "c")
    assert rel is not None
    assert rel.pooled_runs == 1
    assert rel.pooled_attempts == 1


# window: a segment pools at most `window` runs (the newest)


def test_window_pools_only_the_newest_runs_in_segment() -> None:
    # The oldest run fails and the two newer ones pass. window=2 drops the
    # oldest, so the rate covers only the newest two. Truncation keeps the
    # newest runs, not the oldest.
    runs = [
        _make_run("A", "2026-01-01T00:00:00Z", "F"),
        _make_run("B", "2026-01-02T00:00:00Z", "P"),
        _make_run("C", "2026-01-03T00:00:00Z", "P"),
    ]
    rel = pool_case_reliability(runs, "t", "c", window=2)
    assert rel is not None
    assert rel.pooled_runs == 2
    assert rel.pooled_attempts == 2
    assert rel.pooled_passes == 2
    assert rel.rate == 1.0, "truncation drops the failing oldest run"


def test_window_one_keeps_only_the_tip() -> None:
    runs = [
        _make_run("A", "2026-01-01T00:00:00Z", "P"),
        _make_run("B", "2026-01-02T00:00:00Z", "F"),
    ]
    rel = pool_case_reliability(runs, "t", "c", window=1)
    assert rel is not None
    assert rel.pooled_runs == 1
    assert rel.rate == 0.0, "only the newest (failing) run is pooled"


def test_window_caps_segment_at_default_window() -> None:
    # 60 runs in one segment. The default window pools exactly the newest 50.
    runs = [_make_run(f"{i:03d}", f"2026-01-01T00:{i:02d}:00Z", "P") for i in range(60)]
    rel = pool_case_reliability(runs, "t", "c")  # default window
    assert rel is not None
    assert rel.pooled_runs == DEFAULT_WINDOW
    assert rel.pooled_attempts == DEFAULT_WINDOW


def test_a_viewed_run_does_not_evict_a_mainline_run_from_the_window() -> None:
    # `window` bounds mainline runs. A viewed run (a PR or local tip)
    # rides on top and does not count against it. With window=3 and 3 mainline
    # runs, the pool holds all 3 mainline runs plus the viewed one, not 2 plus
    # the viewed one.
    mainline = [
        _make_run("A", "2026-01-01T00:00:00Z", "P"),
        _make_run("B", "2026-01-02T00:00:00Z", "P"),
        _make_run("C", "2026-01-03T00:00:00Z", "F"),
    ]
    viewed = _make_run("D", "2026-01-04T00:00:00Z", "P")
    # Newest first: the viewed run, then mainline from newest to oldest.
    samples = [
        HistoryRun(viewed, viewed.commit, viewed.created_at, off_mainline=True),
        *[HistoryRun(r, r.commit, r.created_at) for r in reversed(mainline)],
    ]
    rel = report_reliability_over(samples, "t", window=3)["c"]
    assert rel.pooled_runs == 3, "all 3 mainline pooled, the viewed run evicts none"
    assert rel.pooled_attempts == 3
    assert rel.pooled_passes == 2, (
        "the off-mainline run's pass stays out of the pooled rate"
    )
    assert len(rel.points) == 4, "the viewed run is charted even though not pooled"
    assert rel.points[-1].off_mainline is True


# --- the per-attempt detail, and the all-tests wrapper ---


def test_the_points_carry_each_attempt_in_the_order_it_ran() -> None:
    """`passes` is a summary of this list, and the dashboard shows the attempts
    individually. Errored attempts are not in it, because they reached no
    verdict to show."""
    rel = pool_case_reliability(
        [_make_run("A", "2026-01-01T00:00:00Z", "FPE")], "t", "c"
    )
    assert rel is not None
    [point] = rel.points

    assert point.attempts == [False, True], "errored attempts are not verdicts"
    assert (point.passed_attempts, point.clean_attempts) == (1, 2)


def test_every_test_in_the_window_is_reported() -> None:
    """The dashboard asks for the whole run at once rather than a call per
    test, so the wrapper must not stop at the first."""
    run = make_eval_run(
        run_id=make_run_id("A"),
        created_at="2026-01-01T00:00:00Z",
        tests={
            "t": RecordedTest(
                cases={"c": make_case_result("P")}, marker=MarkerSettings()
            ),
            "u": RecordedTest(
                cases={"d": make_case_result("F")}, marker=MarkerSettings()
            ),
        },
    )

    reported = report_all_case_reliability_over(build_samples_from_runs([run]))

    assert sorted(reported) == ["t", "u"]
    assert reported["u"]["d"].rate == 0.0


def test_a_test_with_nothing_in_the_current_segment_is_left_out() -> None:
    """An omitted test is not a test with an empty report. A caller that reads
    `result[name]` would raise rather than show nothing."""
    recorded_nothing = make_eval_run(
        run_id=make_run_id("A"),
        created_at="2026-01-01T00:00:00Z",
        tests={"t": RecordedTest(outcome="errored")},
    )

    reported = report_all_case_reliability_over(
        build_samples_from_runs([recorded_nothing])
    )

    assert reported == {}


def test_a_test_no_sample_recorded_reports_nothing() -> None:
    """Asking about a test that never evaluated in this window is a normal
    question from the dashboard, not an error."""
    run = _make_run("A", "2026-01-01T00:00:00Z", "P")
    assert report_reliability_over(build_samples_from_runs([run]), "absent") == {}


def test_wilson_zero_samples_is_zero() -> None:
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_all_fail_is_zero() -> None:
    assert wilson_lower_bound(0, 10) == 0.0


def test_wilson_lower_bound_matches_the_95_percent_limit() -> None:
    """The textbook Wilson 95% lower limit for 5 of 10. This value is what pins
    the confidence level: every other property here holds for any `z`."""
    assert wilson_lower_bound(5, 10) == pytest.approx(0.2366, abs=1e-4)


def test_wilson_perfect_run_is_not_certainty() -> None:
    """A short unbroken streak leaves the true rate well short of 1.0. That gap
    is why the bound is reported beside the raw rate at all."""
    assert wilson_lower_bound(10, 10) < 1.0


def test_wilson_lower_bound_is_below_point_estimate() -> None:
    lb = wilson_lower_bound(5, 10)
    assert 0.0 < lb < 0.5, (
        "a confidence floor sits above zero but below the 0.5 observed rate"
    )


def test_wilson_tightens_with_more_samples() -> None:
    # With the same rate but more samples, the floor rises toward the estimate.
    assert wilson_lower_bound(90, 100) > wilson_lower_bound(9, 10)
