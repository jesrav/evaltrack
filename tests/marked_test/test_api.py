"""`evaltrack.run`: what it translates, what it records, and what it refuses.

These register a translator rather than mock one, so they exercise the dispatch
a third-party runner goes through. For the registry that dispatch walks, see
`tests/translators/test_registry.py`. The rerun and repeat loop is
`test_run_loop.py`, and how a round folds back in is `test_merge.py`.
"""

import asyncio
from typing import Any

import pytest

from evaltrack import run, run_async
from evaltrack import translators as registry
from evaltrack.core.errors import EvalDefinitionError, EvalExecutionError
from evaltrack.core.eval_round import (
    AttemptErrorRecord,
    EvalRound,
    RoundAttempt,
    RoundErrorRecord,
)
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.results import EvaluatorInfo, EvaluatorResult, RunnerInfo
from evaltrack.core.run_record import MarkerSettings
from evaltrack.marked_test import MarkedTest, bind_marked_test
from evaltrack.translators import TranslatorNotFoundError, register

from ..factories import make_attempt, make_round
from .conftest import Bind


class FakeResult:
    """What a runner evaltrack has never heard of hands back."""

    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed


FAKE_RUNNER = "fake-runner"
# Derived rather than written out: the registry matches on the dotted path, so a
# hard-coded one silently stops matching if this module moves.
FAKE_RESULT_TYPE = f"{FakeResult.__module__}.{FakeResult.__qualname__}"


class FakeTranslator:
    """Reads `FakeResult` the way a real translator reads its runner."""

    def translate(self, result: Any) -> EvalRound:
        return EvalRound(
            runner=RunnerInfo(name=FAKE_RUNNER, version="1.2.3"),
            attempts=[make_attempt("c1", assertions={"ok": result.passed})],
        )


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the fake on a copy of the process-wide registry, so the
    registration ends with the test."""
    monkeypatch.setattr(registry, "_registry", dict(registry._registry))  # pyright: ignore[reportPrivateUsage]
    register(FAKE_RUNNER, native_types=(FAKE_RESULT_TYPE,), load=FakeTranslator)


@pytest.fixture
def marked_test(bind: Bind) -> MarkedTest:
    """A `MarkedTest` bound to this thread, as the marker's fixture binds one."""
    return bind()


def _read_case_ids(recorder: EvalRecorder) -> list[str]:
    return list(recorder.to_run_record().tests["tests/test_x.py::test_eval"].cases)


class TestOutsideAMarkedTest:
    def test_record_without_a_running_test_is_refused(self) -> None:
        """Nothing to attribute the eval to, and no score bars to apply, so
        the round would be dropped silently."""
        with pytest.raises(EvalDefinitionError, match="outside a test marked"):
            run(make_round)


@pytest.mark.usefixtures("fake_runner")
class TestTranslatedResults:
    def test_a_runners_result_is_translated_and_recorded(
        self, marked_test: MarkedTest
    ) -> None:
        run(FakeResult)
        assert _read_case_ids(marked_test.recorder) == ["c1"]

    def test_the_runner_is_recorded_with_the_test(
        self, marked_test: MarkedTest
    ) -> None:
        """Which runner produced a test's eval is per test, because one session
        can record several."""
        run(FakeResult)
        recorded = marked_test.recorder.to_run_record()
        runner = recorded.tests["tests/test_x.py::test_eval"].runner
        assert runner is not None
        assert (runner.name, runner.version) == ("fake-runner", "1.2.3")

    def test_a_failing_case_fails_the_test_at_the_call(
        self, marked_test: MarkedTest
    ) -> None:
        """The record and the gate are one step, so a test cannot record
        without a judgment."""
        with pytest.raises(AssertionError, match="c1"):
            run(FakeResult, passed=False)

    def test_a_failing_case_is_still_recorded(self, marked_test: MarkedTest) -> None:
        """The gate raising must not cost the run the evidence of why."""
        with pytest.raises(AssertionError):
            run(FakeResult, passed=False)
        assert _read_case_ids(marked_test.recorder) == ["c1"]


def _hand_built(value: Any) -> EvalRound:
    """One case whose only result carries `value` and nothing else.

    The no-runner path. No translator stands between the value and the model,
    so the value must mean what it means on its own.
    """
    return EvalRound(
        runner=RunnerInfo(name="hand-built"),
        attempts=[
            RoundAttempt(
                case_id="c1",
                inputs="in",
                output="out",
                results={
                    "correct": EvaluatorResult(
                        value=value, evaluator=EvaluatorInfo(name="correct")
                    )
                },
            )
        ],
    )


class TestWithoutATranslator:
    def test_a_round_built_by_hand_is_recorded(self, marked_test: MarkedTest) -> None:
        """A runner with no translator is still usable. Build the model
        directly."""
        run(
            make_round,
            attempts=[make_attempt("hand-built", assertions={"ok": True})],
        )
        assert _read_case_ids(marked_test.recorder) == ["hand-built"]

    @pytest.mark.usefixtures("marked_test")
    def test_a_bare_false_value_fails_the_gate(self) -> None:
        """`EvaluatorResult(value=False)` is how a hand-built failure reads, and
        nothing translates it into a verdict here. Left ungated the case would
        pass on the failure it just recorded."""
        with pytest.raises(AssertionError, match="c1"):
            run(_hand_built, False)

    @pytest.mark.usefixtures("marked_test")
    def test_a_bare_true_value_passes_the_gate(self) -> None:
        run(_hand_built, True)

    def test_an_unknown_result_type_names_what_is_registered(
        self, marked_test: MarkedTest
    ) -> None:
        with pytest.raises(TranslatorNotFoundError, match="pydantic-evals"):
            run(object)


def _hand_built_with(**result_fields: Any) -> EvalRound:
    """One case whose single result carries `result_fields` beside its value."""
    return EvalRound(
        runner=RunnerInfo(name="my-loop"),
        attempts=[
            RoundAttempt(
                case_id="c1",
                results={
                    "quality": EvaluatorResult(
                        value=0.4,
                        evaluator=EvaluatorInfo(name="judge"),
                        **result_fields,
                    )
                },
            )
        ],
    )


@pytest.mark.usefixtures("marked_test")
class TestUnknownResultFields:
    """`EvaluatorResult` admits a field it does not declare, so a stored run from
    a later version still loads. In flight that is a misspelt `bar=` or
    `verdict=`, and the model would keep it as data and gate on nothing."""

    def test_a_misspelt_field_is_refused_naming_the_case_and_the_result(self) -> None:
        with pytest.raises(EvalDefinitionError, match="'quality'.*'c1'.*'barr'"):
            run(_hand_built_with, barr=0.5)

    def test_the_refusal_suggests_the_field_meant(self) -> None:
        with pytest.raises(EvalDefinitionError, match="did you mean 'verdict'"):
            run(_hand_built_with, verdcit=True)

    def test_every_unknown_field_is_named(self) -> None:
        with pytest.raises(EvalDefinitionError, match="'barr', 'weight'"):
            run(_hand_built_with, weight=2, barr=0.5)

    def test_it_is_refused_before_the_marker_bars_the_score(self, bind: Bind) -> None:
        """The marker's bar would gate the score and hide the misspelling behind
        a result that does fail."""
        bind(score_bars={"quality": 0.8})
        with pytest.raises(EvalDefinitionError, match="'barr'"):
            run(_hand_built_with, barr=0.5)

    def test_a_result_with_only_declared_fields_is_recorded(
        self, marked_test: MarkedTest
    ) -> None:
        run(_hand_built_with, runner_bar=0.2)
        assert _read_case_ids(marked_test.recorder) == ["c1"]


@pytest.mark.usefixtures("marked_test")
class TestRefusals:
    def test_a_second_eval_is_refused(self) -> None:
        """Eval identity is the test, so a second one would silently depend on
        call order."""
        run(make_round)
        with pytest.raises(EvalDefinitionError, match="a test records one"):
            run(make_round)

    def test_an_eval_with_no_cases_is_refused(self) -> None:
        """A dataset that loaded to nothing has no failing case for the gate to
        catch, so without this it would always pass."""
        with pytest.raises(EvalDefinitionError, match="no cases"):
            run(make_round, attempts=[])

    def test_a_round_that_died_is_blamed_before_the_dataset(self) -> None:
        """A run the runner ended early reports no attempt and its own error.
        The error is the reason there is nothing to gate, so it is what the
        reader gets."""
        with pytest.raises(EvalExecutionError) as excinfo:
            run(
                make_round,
                attempts=[],
                errors=[
                    RoundErrorRecord(
                        name="error", message="could not reach the provider"
                    )
                ],
            )
        message = str(excinfo.value)
        assert "could not reach the provider" in message, (
            "the runner's own message is what says why the round produced nothing"
        )
        assert "dataset" not in message, "the dataset loaded; blaming it misleads"

    def test_a_crashed_task_is_raised_rather_than_gated(self) -> None:
        """The crash is the root cause. A failing verdict downstream of it
        sends the reader to the wrong place."""
        with pytest.raises(EvalExecutionError, match="task failures"):
            run(
                make_round,
                attempts=[
                    make_attempt("c1", errors=[AttemptErrorRecord(message="boom")])
                ],
            )


def test_a_judge_that_crashed_on_every_case_is_raised_and_still_recorded(
    bind: Bind,
) -> None:
    """An outage leaves the marker's bar matching no score. A report of a
    misnamed bar sends the reader to the marker instead of to the judge. The
    round was paid for either way, so it must reach the run."""
    active = bind(score_bars={"quality": 0.8})
    with pytest.raises(EvalExecutionError, match="LlmJudge"):
        run(
            lambda: make_round(
                attempts=[
                    make_attempt(
                        "c1",
                        errors=[
                            AttemptErrorRecord(
                                message="RuntimeError: 401 Unauthorized",
                                evaluator="LlmJudge",
                            )
                        ],
                    )
                ]
            )
        )
    assert _read_case_ids(active.recorder) == ["c1"], "the round reached the run"


def test_an_eval_that_does_not_re_run_is_refused(bind: Bind) -> None:
    """An eval that hands back what it handed back last time reads like it works
    and quietly spends the whole budget on one set of verdicts. Identity is the
    test. A genuinely flaky eval can fail twice the same way, and that is when
    another round is worth the cost."""
    bind(flake_reruns=2)
    already_ran = make_round(attempts=[make_attempt("c", assertions={"ok": False})])

    def make_report() -> EvalRound:
        return already_ran

    with pytest.raises(EvalDefinitionError, match="did not re-run"):
        run(make_report)


_NOT_AN_EVAL = pytest.mark.parametrize(
    "given",
    [FakeResult(), make_round(), object()],
    ids=["a-runners-result", "an-evaltrack-round", "anything-else"],
)


@pytest.mark.usefixtures("marked_test", "fake_runner")
class TestGivenAResultInsteadOfTheEval:
    """`evaltrack.run(dataset.evaluate_sync(task))` calls the eval and hands
    over what came back. That eval has already run and nothing can ask it for
    another, so every round after the first would re-record it.
    Whatever was given, the refusal shows the call that works."""

    @_NOT_AN_EVAL
    def test_run_refuses_what_it_cannot_call(self, given: Any) -> None:
        with pytest.raises(EvalDefinitionError, match="cannot be called") as excinfo:
            run(given)
        assert "evaltrack.run(dataset.evaluate_sync, task)" in str(excinfo.value)

    @_NOT_AN_EVAL
    def test_run_async_refuses_what_it_cannot_call(self, given: Any) -> None:
        with pytest.raises(EvalDefinitionError, match="cannot be called") as excinfo:
            asyncio.run(run_async(given))
        assert "await evaltrack.run_async(dataset.evaluate, task)" in str(excinfo.value)


class TestEverythingMustBeGateable:
    """A marked test promises a gate, so every result answers pass or fail. One
    that answers neither would pass on a verdict nobody reached, and it makes
    the case, and the test, read as checked where it is not."""

    def test_a_score_with_no_bar_is_refused(self, marked_test: MarkedTest) -> None:
        """A bare number has no pass or fail, so the case cannot fail however
        bad the number is."""
        with pytest.raises(EvalDefinitionError, match="q scored without a bar"):
            run(make_round, attempts=[make_attempt("c1", scores={"q": 0.01})])

    def test_the_refusal_names_every_bare_score_and_how_to_fix_it(
        self, marked_test: MarkedTest
    ) -> None:
        """The fix is per score, so a reader must know which ones to bar. Where
        the bar goes depends on who is reading: a test author sets it on the
        marker, a translator author on the result the runner produced."""
        with pytest.raises(EvalDefinitionError) as excinfo:
            run(
                make_round,
                attempts=[make_attempt("c1", scores={"q": 0.01, "speed": 0.2})],
            )
        message = str(excinfo.value)
        assert "q" in message and "speed" in message
        assert "score_bars" in message, "the fix for a test author is named"
        assert "runner_bar" in message, "the fix for a translator author is named"

    def test_a_marker_bar_is_enough(self) -> None:
        marked_test = MarkedTest(
            "tests/test_x.py::test_eval",
            recorder=EvalRecorder(),
            settings=MarkerSettings(score_bars={"q": 0.5}),
        )
        with bind_marked_test(marked_test):
            with pytest.raises(AssertionError, match="below bar"):
                run(make_round, attempts=[make_attempt("c1", scores={"q": 0.01})])

    def test_a_runners_own_threshold_is_enough(self, marked_test: MarkedTest) -> None:
        """A runner that bars its own metrics needs nothing on the marker, so
        it must not be refused."""
        with pytest.raises(AssertionError, match="below bar"):
            run(
                make_round,
                attempts=[
                    make_attempt("c1", scores={"q": 0.01}, thresholds={"q": 0.5})
                ],
            )

    def test_an_assertion_is_enough(self, marked_test: MarkedTest) -> None:
        run(make_round, attempts=[make_attempt("c1", assertions={"ok": True})])

    def test_a_case_that_recorded_nothing_is_refused(
        self, marked_test: MarkedTest
    ) -> None:
        """The same mistake with nothing to name. A case nobody evaluated
        passes whatever it produced."""
        with pytest.raises(EvalDefinitionError, match="empty recorded no results"):
            run(
                make_round,
                attempts=[
                    make_attempt("judged", assertions={"ok": True}),
                    make_attempt("empty"),
                ],
            )

    def test_an_attempt_that_recorded_nothing_is_refused_beside_one_that_did(
        self,
    ) -> None:
        """Under `repeats=` the case is judged on every attempt, so an empty one
        passes on an empty set of checks. Asked per case rather than per
        attempt, the evaluated half would answer for both."""
        active = MarkedTest(
            "tests/test_x.py::test_eval",
            recorder=EvalRecorder(),
            settings=MarkerSettings(repeats=2),
        )
        with bind_marked_test(active):
            with pytest.raises(EvalDefinitionError, match="half recorded no results"):
                run(
                    make_round,
                    attempts=[
                        make_attempt("half", assertions={"ok": True}),
                        make_attempt("half"),
                    ],
                )

    def test_a_bar_that_misses_a_case_leaves_that_case_refused(self) -> None:
        """The tradeoff for a mixed dataset. A bar gates only the cases that
        produced its score, so a case that produced another one is left bare
        and a wrong answer from it would read as a pass."""
        active = MarkedTest(
            "tests/test_x.py::test_eval",
            recorder=EvalRecorder(),
            settings=MarkerSettings(score_bars={"q": 0.5}),
        )
        with bind_marked_test(active):
            with pytest.raises(EvalDefinitionError, match="speed scored without"):
                run(
                    make_round,
                    attempts=[
                        make_attempt("scored", scores={"q": 0.9}),
                        make_attempt("other", scores={"speed": 0.2}),
                    ],
                )

    def test_a_round_where_everything_gates_is_left_alone(
        self, marked_test: MarkedTest
    ) -> None:
        run(
            make_round,
            attempts=[
                make_attempt("c1", assertions={"ok": True}),
                make_attempt("c2", scores={"q": 0.9}, thresholds={"q": 0.5}),
            ],
        )
