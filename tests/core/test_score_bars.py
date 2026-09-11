"""What the marker's score bars do to a result, and what they refuse."""

import json

import pytest

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import AttemptErrorRecord
from evaltrack.core.results import EvaluatorResult
from evaltrack.core.score_bars import (
    apply_score_bars,
    raise_on_unmatched_score_bars,
)

from ..factories import make_attempt, make_round


def _make_verdict(
    case: str,
    *,
    expected_to_pass: bool,
    scores: dict[str, float] | None = None,
    assertions: dict[str, bool] | None = None,
    thresholds: dict[str, float] | None = None,
    bars: dict[str, float] | None = None,
) -> object:
    """Build one `pytest.param` row for `_VERDICTS` below. The row holds an
    attempt that produced `scores` and `assertions`, the declared `bars`, and
    the verdict the pass rule must reach (`expected_to_pass`).

    The return type is `object` because pytest exports no public name for the
    type of `pytest.param`.

    The arguments are keyword-only because `scores`, `thresholds` and `bars` are
    all `dict[str, float]`. Swapped by position they still read well and still
    type-check, and `pytest.param` takes `*values: object`, so it checks
    nothing. The test id comes from `expected_to_pass`, so a row cannot claim
    one verdict in its name and assert another.
    """
    return pytest.param(
        scores or {},
        assertions or {},
        thresholds or {},
        bars or {},
        expected_to_pass,
        id=f"{case}-{'passes' if expected_to_pass else 'fails'}",
    )


_VERDICTS = [
    _make_verdict(
        "true-assertion-no-bars", expected_to_pass=True, assertions={"x": True}
    ),
    _make_verdict("false-assertion", expected_to_pass=False, assertions={"x": False}),
    _make_verdict("bar-met", expected_to_pass=True, scores={"q": 0.9}, bars={"q": 0.8}),
    # A bar is the value to reach, not to beat, so a score sitting on it passes.
    _make_verdict(
        "bar-met-exactly", expected_to_pass=True, scores={"q": 0.8}, bars={"q": 0.8}
    ),
    _make_verdict(
        "bar-missed", expected_to_pass=False, scores={"q": 0.5}, bars={"q": 0.8}
    ),
    # Bars are scoped. A case that produced no barred score is not gated on it,
    # and passes on whatever else it recorded. A typo, where no case at all
    # produces the score, is caught earlier by `assert_cases_passed`.
    _make_verdict(
        "bar-the-case-has-no-score-for",
        expected_to_pass=True,
        assertions={"x": True},
        bars={"q": 0.8},
    ),
    # A runner that bars its own metrics gates without a marker bar.
    _make_verdict(
        "runner-threshold-met",
        expected_to_pass=True,
        scores={"q": 0.9},
        thresholds={"q": 0.8},
    ),
    _make_verdict(
        "runner-threshold-missed",
        expected_to_pass=False,
        scores={"q": 0.5},
        thresholds={"q": 0.8},
    ),
    # The test's bar wins over the runner's, in both directions.
    _make_verdict(
        "bar-relaxes-runner-threshold",
        expected_to_pass=True,
        scores={"q": 0.6},
        thresholds={"q": 0.8},
        bars={"q": 0.5},
    ),
    _make_verdict(
        "bar-tightens-runner-threshold",
        expected_to_pass=False,
        scores={"q": 0.6},
        thresholds={"q": 0.5},
        bars={"q": 0.9},
    ),
]


@pytest.mark.parametrize(
    ("scores", "assertions", "thresholds", "bars", "expected_to_pass"), _VERDICTS
)
def test_pass_rule(
    scores: dict[str, float],
    assertions: dict[str, bool],
    thresholds: dict[str, float],
    bars: dict[str, float],
    expected_to_pass: bool,
) -> None:
    """The shared per-attempt rule: every result that gates must pass, whether
    the runner judged it or a bar decides it.

    The name states the rule, not a verdict. Most rows here are cases the rule
    must deny, so a name like `test_the_attempt_passed[bar-missed]` reads as a
    contradiction.
    """
    # The bars reach the result the same way the pipeline puts them there, so
    # this covers the resolution as well as the rule.
    eval_round = apply_score_bars(
        make_round(
            attempts=[
                make_attempt(
                    "c", scores=scores, assertions=assertions, thresholds=thresholds
                )
            ]
        ),
        bars,
    )
    [attempt] = eval_round.attempts
    assert attempt.passed is expected_to_pass


class TestAMarkerBarReplacesTheRunners:
    """The marker's bar is the project's own policy for the test, so it is the
    one that gates."""

    def test_the_runners_own_bar_is_kept_beside_the_marker_s(self) -> None:
        """The record has to say whose bar gated the score, and the runner's
        verdict is recoverable from the bar it was derived from."""
        eval_round = apply_score_bars(
            make_round(
                attempts=[make_attempt("c1", scores={"q": 0.6}, thresholds={"q": 0.8})]
            ),
            {"q": 0.5},
        )
        result = eval_round.attempts[0].results["q"]
        assert (result.bar, result.runner_bar, result.marker_bar) == (0.5, 0.8, 0.5)

    def test_each_bar_is_recorded_by_whoever_set_it(self) -> None:
        """Neither field says anything about the other one. A runner's bar the
        marker left alone is still the runner's, and a reader of the ordinary
        result must not take an empty `runner_bar` for a runner that barred
        nothing."""
        eval_round = apply_score_bars(
            make_round(
                attempts=[
                    make_attempt(
                        "c1",
                        scores={"kept": 0.6, "bare": 0.6},
                        thresholds={"kept": 0.8},
                    )
                ]
            ),
            {"bare": 0.5},
        )
        kept, bare = (eval_round.attempts[0].results[name] for name in ("kept", "bare"))
        assert (kept.bar, kept.runner_bar, kept.marker_bar) == (0.8, 0.8, None)
        assert (bare.bar, bare.runner_bar, bare.marker_bar) == (0.5, None, 0.5)

    def test_the_replacement_is_unchanged_by_being_loaded_again(self) -> None:
        """`bar` is derived on every validation, so a load re-derives it from
        the two stored bars. Reading the runner's over the marker's there would
        fail a case the recorded run passed."""
        eval_round = apply_score_bars(
            make_round(
                attempts=[make_attempt("c1", scores={"q": 0.6}, thresholds={"q": 0.8})]
            ),
            {"q": 0.5},
        )
        result = eval_round.attempts[0].results["q"]
        stored = json.loads(result.model_dump_json())
        assert EvaluatorResult.model_validate(stored) == result


class TestRaiseOnUnmatchedScoreBars:
    """A bar can only gate a number. A bar aimed at anything else would be
    accepted and then do nothing, which reads like a passing eval."""

    def test_a_bar_naming_no_result_is_refused(self) -> None:
        eval_round = make_round(attempts=[make_attempt("c1", scores={"quality": 0.9})])
        with pytest.raises(EvalDefinitionError, match="no case produced"):
            raise_on_unmatched_score_bars(eval_round, {"quallity": 0.8})

    def test_a_bar_on_an_eval_that_scores_nothing_is_refused(self) -> None:
        """An eval of assertions only produces no number anywhere, so a bar on
        it gates nothing. That reads as a passing eval rather than a
        misconfigured one."""
        eval_round = make_round(attempts=[make_attempt("c1", assertions={"x": True})])
        with pytest.raises(EvalDefinitionError, match="no case produced"):
            raise_on_unmatched_score_bars(eval_round, {"quality": 0.8})

    def test_a_matching_bar_is_fine(self) -> None:
        eval_round = make_round(attempts=[make_attempt("c1", scores={"quality": 0.9})])
        raise_on_unmatched_score_bars(eval_round, {"quality": 0.8})

    def test_a_crashed_task_is_not_reported_as_a_misnamed_bar(self) -> None:
        """A task that raised produced no scores, so every bar looks misnamed.
        That report would bury the crash that caused it."""
        eval_round = make_round(
            attempts=[make_attempt("c1", errors=[AttemptErrorRecord(message="boom")])]
        )
        raise_on_unmatched_score_bars(eval_round, {"quality": 0.8})

    def test_a_crashed_evaluator_is_not_reported_as_a_misnamed_bar(self) -> None:
        """A judge that raised on every case scored nothing either. Its own
        failure is what makes the bar look misnamed."""
        eval_round = make_round(
            attempts=[
                make_attempt(
                    "c1",
                    errors=[AttemptErrorRecord(message="boom", evaluator="LlmJudge")],
                )
            ]
        )
        raise_on_unmatched_score_bars(eval_round, {"quality": 0.8})
