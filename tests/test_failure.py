"""What a failed eval raises, and the message it raises with.

Nothing here tests the pass rule. `assert_cases_passed` takes no bars and
decides nothing itself: it reads `RoundAttempt.passed`, whose rule
`tests/core/test_score_bars.py` enumerates. What is left for this module is
rolling attempts up per case and saying what went wrong.
"""

import pytest

from evaltrack.core.errors import EvalExecutionError
from evaltrack.core.eval_round import AttemptErrorRecord, EvalRound, RoundErrorRecord
from evaltrack.core.score_bars import apply_score_bars
from evaltrack.failure import assert_cases_passed, raise_on_round_errors

from .factories import make_attempt, make_round


def _make_task_failure(message: str = "boom") -> AttemptErrorRecord:
    return AttemptErrorRecord(message=message)


class TestRaiseOnRoundErrors:
    """The raised message names each case and evaluator with its error, so the
    reader starts from the cause instead of a bare count."""

    def test_passes_clean_round(self) -> None:
        raise_on_round_errors(
            make_round(attempts=[make_attempt("c1", assertions={"x": True})])
        )

    def test_task_failure_names_the_case_and_the_error(self) -> None:
        eval_round = make_round(
            attempts=[make_attempt("c1", errors=[_make_task_failure()])]
        )
        with pytest.raises(EvalExecutionError, match=r"task failures: c1 \(boom\)"):
            raise_on_round_errors(eval_round)

    def test_multiple_task_failures_are_each_named(self) -> None:
        eval_round = make_round(
            attempts=[
                make_attempt("c1", errors=[_make_task_failure()]),
                make_attempt(
                    "c2", errors=[AttemptErrorRecord(message="ValueError: bad date")]
                ),
            ]
        )
        with pytest.raises(
            EvalExecutionError,
            match=r"task failures: c1 \(boom\), c2 \(ValueError: bad date\)",
        ):
            raise_on_round_errors(eval_round)

    def test_long_error_message_is_cut_to_one_line(self) -> None:
        eval_round = make_round(
            attempts=[
                make_attempt(
                    "c1",
                    errors=[AttemptErrorRecord(message="boom\nTraceback: ...\n  more")],
                )
            ]
        )
        with pytest.raises(EvalExecutionError) as exc_info:
            raise_on_round_errors(eval_round)
        assert "Traceback" not in str(exc_info.value)

    def test_round_level_failure_is_named(self) -> None:
        eval_round = make_round(
            errors=[RoundErrorRecord(name="MyEvaluator", message="oops")]
        )
        with pytest.raises(
            EvalExecutionError, match=r"round-level failures: MyEvaluator"
        ):
            raise_on_round_errors(eval_round)

    def test_per_case_evaluator_failure_names_case_and_evaluator(self) -> None:
        eval_round = make_round(
            attempts=[
                make_attempt(
                    "c1",
                    errors=[
                        AttemptErrorRecord(message="oops", evaluator="MyEvaluator")
                    ],
                )
            ]
        )
        with pytest.raises(
            EvalExecutionError,
            match=r"per-case evaluator failures: c1: MyEvaluator \(oops\)",
        ):
            raise_on_round_errors(eval_round)


class TestAssertCasesPassed:
    """`assert_cases_passed` passes only when every case passed."""

    def test_passes_when_all_assertions_true(self) -> None:
        assert_cases_passed(
            [
                make_attempt("c1", assertions={"x": True}),
                make_attempt("c2", assertions={"x": True}),
            ]
        )

    def test_raises_on_any_false_assertion(self) -> None:
        eval_round = make_round(
            attempts=[
                make_attempt("c1", assertions={"x": True}),
                make_attempt("c2", assertions={"x": False}),
            ]
        )
        with pytest.raises(AssertionError, match="c2"):
            assert_cases_passed(eval_round.attempts)

    def test_score_bar_scoped_still_fails_cases_with_the_score(self) -> None:
        """The scope only spares cases without the score. A case that did
        produce the score and missed the bar still fails, while its score-less
        sibling passes."""
        eval_round = make_round(
            attempts=[
                make_attempt("below_bar", scores={"q": 0.5}, assertions={"x": True}),
                make_attempt("no_score", assertions={"x": True}),
            ]
        )
        with pytest.raises(AssertionError, match="below_bar"):
            assert_cases_passed(apply_score_bars(eval_round, {"q": 0.8}).attempts)

    def test_passes_on_empty_round(self) -> None:
        """An empty list of attempts passes. A caller can gate any set of
        attempts it built, and an empty set holds no failing case. `MarkedTest`
        is what refuses a dataset that is empty by mistake."""
        assert_cases_passed([])

    def test_fails_case_with_crashed_evaluator(self) -> None:
        """An errored attempt never passes, so the case fails even though the
        gate does not describe the error."""
        eval_round = make_round(
            attempts=[
                make_attempt(
                    "c1",
                    errors=[
                        AttemptErrorRecord(message="oops", evaluator="MyEvaluator")
                    ],
                )
            ]
        )
        with pytest.raises(AssertionError, match="c1"):
            assert_cases_passed(eval_round.attempts)


def _read_failure_message(
    eval_round: EvalRound, score_bars: dict[str, float] | None = None
) -> str:
    """The text `assert_cases_passed` raises for attempts it rejects.

    The bars go on the results first, the way `MarkedTest` puts them there.
    `assert_cases_passed` takes no bars of its own.
    """
    with pytest.raises(AssertionError) as exc_info:
        assert_cases_passed(apply_score_bars(eval_round, score_bars or {}).attempts)
    return str(exc_info.value)


class TestAssertCasesPassedMessage:
    """A bare list of case names makes the reader open another tool for every
    failure, so the message must say what each case got wrong."""

    def test_names_failed_assertion_with_its_reason(self) -> None:
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt(
                        "c1",
                        assertions={"correct": False},
                        reasons={"correct": "said blue, expected red"},
                    )
                ]
            )
        )
        assert "c1: correct (said blue, expected red)" in message

    def test_bare_assertion_shows_only_its_name(self) -> None:
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt(
                        "c1", assertions={"correct": False}, reasons={"correct": None}
                    )
                ]
            )
        )
        assert message == "1 failing case(s): c1: correct"

    def test_passing_assertion_is_left_out(self) -> None:
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt(
                        "c1",
                        assertions={"grounded": True, "correct": False},
                        reasons={"correct": "said blue"},
                    )
                ]
            )
        )
        assert "correct (said blue)" in message
        assert "grounded" not in message

    def test_score_bar_miss_shows_value_and_bar(self) -> None:
        message = _read_failure_message(
            make_round(attempts=[make_attempt("c1", scores={"quality": 0.42})]),
            score_bars={"quality": 0.8},
        )
        assert "c1: quality=0.42 below bar 0.8" in message

    def test_met_score_bar_is_left_out(self) -> None:
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt(
                        "c1",
                        scores={"quality": 0.9, "brevity": 0.1},
                        assertions={"correct": False},
                        reasons={"correct": None},
                    )
                ]
            ),
            score_bars={"quality": 0.8, "brevity": 0.5},
        )
        assert "brevity=0.1 below bar 0.5" in message
        assert "quality" not in message

    def test_runner_bar_miss_shows_value_and_bar(self) -> None:
        """The runner's own bar, with no verdict beside it, is what failed the
        result, so it is what the message reports."""
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt(
                        "c1", scores={"quality": 0.42}, thresholds={"quality": 0.8}
                    )
                ]
            )
        )
        assert message == "1 failing case(s): c1: quality=0.42 below bar 0.8"

    def test_verdict_miss_shows_its_reason(self) -> None:
        """A verdict names no bar to report, so the judge's reason is the only
        thing that says why the case failed."""
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt(
                        "c1",
                        assertions={"quality": False},
                        reasons={"quality": "judge said no"},
                    )
                ]
            )
        )
        assert message == "1 failing case(s): c1: quality (judge said no)"

    def test_long_reason_is_cut_to_one_line(self) -> None:
        """An LLM judge can answer with a paragraph. The whole answer buries
        every other failing case."""
        reason = "verdict wrong " + "detail " * 100 + "\ntrailing line"
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt(
                        "c1", assertions={"correct": False}, reasons={"correct": reason}
                    )
                ]
            )
        )
        assert "verdict wrong" in message
        assert "trailing line" not in message
        assert all(len(line) <= 200 for line in message.splitlines()), (
            "a long reason is cut, not printed whole"
        )

    def test_first_line_names_every_failing_case(self) -> None:
        """pytest's short summary shows only the first line, so the case names
        must be on it. A reader opens a CI log for that summary."""
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt("c1", assertions={"correct": False}),
                    make_attempt("c3", assertions={"correct": False}),
                ]
            )
        )
        assert message.splitlines()[0] == "2 failing case(s): c1, c3"

    def test_one_line_per_failing_case(self) -> None:
        message = _read_failure_message(
            make_round(
                attempts=[
                    make_attempt("c1", assertions={"correct": False}),
                    make_attempt("c2", assertions={"correct": True}),
                    make_attempt("c3", assertions={"correct": False}),
                ]
            )
        )
        lines = message.splitlines()
        assert lines[0] == "2 failing case(s): c1, c3"
        assert len(lines) == 3, "one summary line plus one line per failing case"
        assert "c2" not in message, "the passing case is left out"


class TestAssertCasesPassedRepeat:
    """A case run several times in one round passes only when *every* attempt
    passes. Attempts of one case share a case id."""

    def test_passes_when_all_attempts_pass(self) -> None:
        assert_cases_passed(
            [make_attempt("c", assertions={"passed": True}) for _ in range(3)]
        )

    def test_fails_when_any_attempt_fails(self) -> None:
        # 2 of 3 pass, but every attempt must pass, so the case fails.
        eval_round = make_round(
            attempts=[
                make_attempt("c", assertions={"passed": ok})
                for ok in (True, False, True)
            ]
        )
        with pytest.raises(AssertionError):
            assert_cases_passed(eval_round.attempts)

    def test_message_lists_a_shared_reason_once(self) -> None:
        """Attempts of one case usually fail the same way. One phrase per
        attempt pads the message and adds nothing."""
        eval_round = make_round(
            attempts=[make_attempt("c", assertions={"passed": False}) for _ in range(2)]
        )
        assert _read_failure_message(eval_round) == "1 failing case(s): c: passed (t)"
