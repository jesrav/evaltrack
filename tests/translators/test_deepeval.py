"""The DeepEval translator: what it reads out of an `EvaluationResult`, and the
conventions of that runner it pins.

Everything runner-specific evaltrack knows lives behind this translator, so this
is where a DeepEval release that renames or reshapes something must fail.

Every result here comes from a real `evaluate()` call. The metrics are scripted
so the run needs no API key, but they are real `BaseMetric` subclasses put
through DeepEval's own machinery, which is what fills in the shapes the
translator reads.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from deepeval.dataset import EvaluationDataset, Golden
from deepeval.evaluate import evaluate
from deepeval.evaluate.configs import (
    AsyncConfig,
    CacheConfig,
    DisplayConfig,
    ErrorConfig,
)
from deepeval.metrics import BaseMetric, GEval
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams
from deepeval.tracing import observe, update_current_span

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import EvalRound
from evaltrack.core.score_bars import apply_score_bars
from evaltrack.translators.deepeval import (
    TRANSLATOR,
    raise_on_duplicate_case_ids,
    raise_on_duplicate_metric_names,
)

from ..factories import read_case_ids
from .contract import assert_translator_contract


class Scripted(BaseMetric):
    """A metric that answers with the score it was built with.

    `threshold=None` is DeepEval's own way of spelling "no gate", so nothing
    but the marker's `score_bars` can fail the case.
    """

    def __init__(
        self,
        score: float | None = 1.0,
        *,
        name: str = "Scripted",
        threshold: float | None = 0.5,
        strict_mode: bool = False,
        flaky: bool = False,
        raises: str | None = None,
    ) -> None:
        self.given_name = name
        self.score = score
        self.threshold = threshold
        self.strict_mode = strict_mode
        self.flaky = flaky
        self.raises = raises
        self.reason = f"scored {score}"

    def measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
        if self.raises:
            raise RuntimeError(self.raises)
        return self.score or 0.0

    async def a_measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    # DeepEval types `BaseMetric.__name__` as the literal "Base Metric", so
    # every metric that names itself is an incompatible override.
    @property
    def __name__(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.given_name


class LowerIsBetter(Scripted):
    """A metric that answers on its own comparison rather than the threshold's.

    `is_successful()` is public and not abstract, so this is a supported way to
    write a DeepEval metric: the score is a cost or a latency, and clearing the
    threshold means staying under it.
    """

    def is_successful(self) -> bool:
        self.success = (self.score or 0.0) <= (self.threshold or 0.0)
        return self.success


class ScriptedJudge(DeepEvalBaseLLM):
    """Replaces the model an LLM-as-judge metric calls.

    DeepEval asks a judge for a pydantic model rather than text, so answering
    means filling in whichever schema the metric passed.
    """

    def __init__(self, score: int) -> None:
        self.score = score
        super().__init__(model="scripted-judge")

    def load_model(self, *args: Any, **kwargs: Any) -> "ScriptedJudge":
        return self

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        return "scripted-judge"

    def generate(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        fields = set(getattr(schema, "model_fields", {}))
        if "steps" in fields:
            return schema(steps=["Read it.", "Judge it."])
        return schema(score=self.score, reason="scripted verdict")

    async def a_generate(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        return self.generate(prompt, schema=schema, **kwargs)


@pytest.fixture(autouse=True)
def _run_from_a_temp_directory(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """`evaluate()` writes its last run to `./.deepeval`, so it must not run in
    the directory the suite started from."""
    monkeypatch.chdir(tmp_path)
    yield


def _evaluate(cases: list[LLMTestCase], *, metrics: list[Any] | None = None) -> Any:
    """A real DeepEval run, returning `evaluate`'s own result so dispatch sees the
    type a user passes."""
    return evaluate(
        test_cases=cases,
        metrics=metrics if metrics is not None else [Scripted()],
        async_config=AsyncConfig(run_async=False),
        display_config=DisplayConfig(
            show_indicator=False, print_results=False, inspect_after_run=False
        ),
        cache_config=CacheConfig(write_cache=False),
        # A metric that raises is data the translator has to read. Left at the
        # default, DeepEval re-raises it and there is no result at all.
        error_config=ErrorConfig(ignore_errors=True),
    )


def _translate(
    cases: list[LLMTestCase], *, metrics: list[Any] | None = None
) -> EvalRound:
    return TRANSLATOR.translate(_evaluate(cases, metrics=metrics))


def _case(name: str, **fields: Any) -> LLMTestCase:
    fields.setdefault("input", f"ask {name}")
    fields.setdefault("actual_output", f"answer {name}")
    return LLMTestCase(name=name, **fields)


# DeepEval ships `observe` untyped, so pyright drops the signature it wraps.
@observe(metrics=[Scripted()])  # pyright: ignore[reportUntypedFunctionDecorator]
def _unnamed_component(question: str) -> str:
    """A scored span with no name of its own. DeepEval names it after this
    function, on every golden that reaches it."""
    update_current_span(
        test_case=LLMTestCase(input=question, actual_output=f"answer {question}")
    )
    return f"answer {question}"


@observe(metrics=[Scripted()])  # pyright: ignore[reportUntypedFunctionDecorator]
def _named_component(question: str) -> str:
    """The same span, named per golden."""
    update_current_span(
        name=f"{question}/component",
        test_case=LLMTestCase(input=question, actual_output=f"answer {question}"),
    )
    return f"answer {question}"


def _golden(name: str, *, question: str) -> Golden:
    # pyright does not see DeepEval's default for `multimodal`, so pass it.
    return Golden(name=name, input=question, multimodal=False)


def _traced_run(goldens: list[Golden], *, component: Callable[[str], str]) -> Any:
    """A real traced DeepEval run.

    `evals_iterator()` yields goldens and *returns* the result, so the result is
    only reachable from the `StopIteration` that ends it.
    """
    rounds = EvaluationDataset(goldens=goldens).evals_iterator(
        async_config=AsyncConfig(run_async=False),
        display_config=DisplayConfig(
            show_indicator=False, print_results=False, inspect_after_run=False
        ),
        cache_config=CacheConfig(write_cache=False),
    )
    while True:
        try:
            golden = next(rounds)
        except StopIteration as finished:
            return finished.value
        component(golden.input)


def test_the_translator_holds_the_contract() -> None:
    """The properties every translator holds, checked against a real result.

    `tests/translators/contract.py` holds them. The dataset has two cases with
    different inputs, because the collapsed-ids check needs them, and a metric
    that raised on one of them, because the result-versus-error check does.
    """
    result = _evaluate(
        [_case("first"), _case("second")],
        metrics=[Scripted(0.9), Scripted(name="Broken", raises="judge exploded")],
    )
    assert_translator_contract(TRANSLATOR, result, name="deepeval")


class TestResults:
    def test_a_metric_with_a_threshold_carries_it_as_the_bar(self) -> None:
        """DeepEval's threshold is a bar in evaltrack's sense. The bar is what
        lets the dashboard show the score against what it had to clear, and the
        verdict it reaches is the one `success` reports."""
        [attempt] = _translate(
            [_case("c")], metrics=[Scripted(0.2, threshold=0.5)]
        ).attempts
        result = attempt.results["Scripted"]
        assert (result.value, result.verdict, result.bar) == (0.2, False, 0.5)

    def test_the_marker_s_bar_replaces_the_threshold(self) -> None:
        """DeepEval's metrics threshold themselves by default, so nearly every
        result arrives barred. A bar the marker declares has to win, or the
        case keeps failing at a threshold the project never chose."""
        eval_round = apply_score_bars(
            _translate([_case("c")], metrics=[Scripted(0.2, threshold=0.5)]),
            {"Scripted": 0.1},
        )
        [attempt] = eval_round.attempts
        result = attempt.results["Scripted"]
        assert (result.bar, result.runner_bar) == (0.1, 0.5)
        assert attempt.passed

    def test_a_metric_with_no_threshold_is_the_score_score_bars_reaches(self) -> None:
        """The only DeepEval result the marker's `score_bars` can reach: a
        metric that set no threshold left the bar free. It arrives unjudged and
        needs the marker's bar, which the gate refuses to do without.

        It rides along with a thresholded metric because DeepEval refuses a run
        in which nothing at all can fail.
        """
        round_ = _translate(
            [_case("c")],
            metrics=[Scripted(), Scripted(0.2, name="Bare", threshold=None)],
        )
        [attempt] = round_.attempts
        assert (attempt.results["Bare"].verdict, attempt.results["Bare"].bar) == (
            None,
            None,
        )
        barred = apply_score_bars(round_, {"Bare": 0.5}).attempts[0].results["Bare"]
        assert (barred.verdict, barred.bar) == (False, 0.5)

    def test_the_metrics_reason_travels_with_the_result(self) -> None:
        [attempt] = _translate([_case("c")], metrics=[Scripted(0.7)]).attempts
        assert attempt.results["Scripted"].reason == "scored 0.7"

    def test_a_metric_that_raised_is_an_error_and_not_a_result(self) -> None:
        """DeepEval keeps a metric that raised as data, with no score. An
        attempt with an error never passes, and a case cannot both be judged
        and have failed to be judged."""
        [attempt] = _translate(
            [_case("c")], metrics=[Scripted(name="Broken", raises="judge exploded")]
        ).attempts
        assert attempt.results == {}
        assert attempt.errors[0].evaluator == "Broken"
        assert "judge exploded" in attempt.errors[0].message
        assert not attempt.passed

    def test_a_metric_that_produced_no_score_is_an_error_naming_it(self) -> None:
        """A metric that came back with nothing has nothing to gate on, so it
        is an error rather than a silently missing result."""
        [attempt] = _translate([_case("c")], metrics=[Scripted(None)]).attempts
        assert attempt.results == {}
        assert attempt.errors[0].evaluator == "Scripted"

    def test_strict_mode_is_recorded_as_how_the_metric_was_configured(self) -> None:
        """Strict mode changes what the number means, so the score is unreadable
        without it. Nothing else DeepEval reports about a metric is a setting."""
        strict = _translate([_case("c")], metrics=[Scripted(strict_mode=True)]).attempts
        assert strict[0].results["Scripted"].evaluator.arguments == {
            "strict_mode": True
        }
        plain = _translate([_case("c")]).attempts
        assert plain[0].results["Scripted"].evaluator.arguments is None


class TestFlakyMetrics:
    """DeepEval excuses a metric marked `flaky=True` from deciding its case, so
    its threshold gates nothing there and nothing else about it is a verdict.
    evaltrack gates on every result, so the round is refused.

    A flaky metric always rides along with a thresholded one, because DeepEval
    refuses a run in which nothing but flaky metrics can fail.
    """

    def _flaky(self, metric: Any) -> EvalRound:
        return _translate([_case("c")], metrics=[Scripted(), metric])

    def test_deepeval_passes_a_case_whose_flaky_metric_failed(self) -> None:
        """The runner convention the rest of this suite rests on."""
        result = _evaluate(
            [_case("c")],
            metrics=[Scripted(), Scripted(0.4, name="Bias", threshold=0.9, flaky=True)],
        )
        [case] = result.test_results
        bias = next(m for m in case.metrics_data if m.name == "Bias")
        assert (bias.success, case.success) == (False, True), (
            "DeepEval stopped excusing a flaky metric from its case's verdict, "
            "so recording one without a bar no longer follows the runner"
        )

    def test_a_flaky_metric_is_refused(self) -> None:
        with pytest.raises(EvalDefinitionError, match="'Bias'") as refusal:
            self._flaky(Scripted(0.4, name="Bias", threshold=0.9, flaky=True))
        assert "flaky" in str(refusal.value)

    def test_the_refusal_names_the_way_out(self) -> None:
        """A reader has to know that the flag is what to change, since the
        score and the threshold both look gateable."""
        with pytest.raises(EvalDefinitionError) as refusal:
            self._flaky(Scripted(0.4, name="Bias", threshold=0.9, flaky=True))
        assert "Unset flaky" in str(refusal.value)

    def test_a_flaky_metric_is_refused_for_being_flaky(self) -> None:
        """Not for contradicting its threshold, which the flag entitles it to
        do. The two refusals name different things to change."""
        with pytest.raises(EvalDefinitionError) as refusal:
            self._flaky(LowerIsBetter(3.5, name="Latency", threshold=2.0, flaky=True))
        assert "flaky" in str(refusal.value)
        assert "success=" not in str(refusal.value)


class TestAVerdictTheThresholdContradicts:
    """`is_successful()` is an override point, so a metric can answer on
    something other than `score >= threshold`. Reading the threshold as a bar
    then records the opposite of what DeepEval concluded, and a real failure
    lands green. evaltrack cannot tell which of the two answers is right, so it
    refuses the round instead of picking one.
    """

    def test_a_metric_whose_success_contradicts_its_threshold_is_refused(self) -> None:
        result = _evaluate(
            [_case("c")], metrics=[LowerIsBetter(3.5, name="Latency", threshold=2.0)]
        )
        [metric] = result.test_results[0].metrics_data
        assert (metric.score, metric.threshold, metric.success) == (3.5, 2.0, False), (
            "DeepEval stopped reporting what the metric's own is_successful() "
            "answered, so the refusal below no longer guards anything real"
        )
        with pytest.raises(EvalDefinitionError, match="'Latency'") as refusal:
            TRANSLATOR.translate(result)
        assert "3.5" in str(refusal.value)
        assert "2.0" in str(refusal.value)
        assert "success=False" in str(refusal.value)

    def test_a_metric_whose_success_agrees_is_recorded_unchanged(self) -> None:
        """The ordinary DeepEval metric, which the refusal must leave alone,
        whichever side of its threshold it lands on."""
        for score, verdict in ((0.2, False), (0.9, True)):
            [attempt] = _translate(
                [_case("c")], metrics=[Scripted(score, threshold=0.5)]
            ).attempts
            result = attempt.results["Scripted"]
            assert (result.value, result.runner_bar, result.verdict) == (
                score,
                0.5,
                verdict,
            )


class TestLlmJudge:
    """A real `GEval`, driven by a scripted judge instead of a provider.

    This is the shape DeepEval users actually write, and the only one that
    fills in the judge, its cost and its reasoning.
    """

    def _judged(self, score: int = 8) -> EvalRound:
        metric = GEval(
            name="Helpfulness",
            criteria="Does the answer help?",
            evaluation_params=[
                SingleTurnParams.INPUT,
                SingleTurnParams.ACTUAL_OUTPUT,
            ],
            model=ScriptedJudge(score),
            threshold=0.7,
        )
        return _translate([_case("c")], metrics=[metric])

    def test_a_geval_metric_reports_under_its_suffixed_name(self) -> None:
        """DeepEval names a `GEval` result `"<name> [GEval]"`, not the name
        passed to it. A `score_bars` key has to match what is recorded, so the
        suffix is not cosmetic."""
        [attempt] = self._judged().attempts
        assert list(attempt.results) == ["Helpfulness [GEval]"]

    def test_the_judge_that_scored_it_is_recorded_as_a_detail(self) -> None:
        """Which model judged, and what it reasoned, decide nothing, so neither
        is a result. Both are what a failing case is read with."""
        [attempt] = self._judged().attempts
        details = attempt.results["Helpfulness [GEval]"].details
        assert details["evaluation_model"] == "scripted-judge"
        assert "Evaluation Steps" in details["verbose_logs"]


class TestCases:
    def test_the_case_name_is_the_id(self) -> None:
        eval_round = _translate([_case("alpha"), _case("beta")])
        assert read_case_ids(eval_round) == ["alpha", "beta"]
        assert eval_round.runner.name == "deepeval"
        assert eval_round.runner.version is not None

    def test_what_the_case_was_given_and_produced_travels_with_it(self) -> None:
        [attempt] = _translate(
            [
                _case(
                    "c",
                    input="ask",
                    actual_output="said",
                    expected_output="wanted",
                    metadata={"topic": "billing"},
                )
            ]
        ).attempts
        assert (attempt.inputs, attempt.output) == ("ask", "said")
        assert attempt.expected_output == "wanted"
        assert attempt.metadata == {"topic": "billing"}

    def test_the_retrieval_material_is_recorded_as_details(self) -> None:
        """A RAG case is unreadable without what was retrieved, and none of it
        is a judgment."""
        [attempt] = _translate(
            [_case("c", context=["ground truth"], retrieval_context=["chunk 1"])]
        ).attempts
        assert attempt.details == {
            "context": ["ground truth"],
            "retrieval_context": ["chunk 1"],
        }

    def test_a_case_that_retrieved_nothing_adds_no_key(self) -> None:
        assert _translate([_case("c")]).attempts[0].details == {}


class TestDuplicateCaseIds:
    """A case arrives unnamed from two directions, an `LLMTestCase` under pytest
    and a scored span on a traced run. Either way a whole dataset arrives under
    one name. Recorded as it stands, every case merges into one row with one
    pass-rate history.

    This suite runs under pytest, so these tests reproduce the trap rather than
    describing it.
    """

    def test_cases_left_unnamed_under_pytest_are_refused(self) -> None:
        result = _evaluate(
            [
                LLMTestCase(input="a", actual_output="a"),
                LLMTestCase(input="b", actual_output="b"),
            ]
        )
        names = {case.name for case in result.test_results}
        assert len(names) == 1, (
            "DeepEval stopped naming unnamed cases after the pytest test, so "
            "the refusal below no longer guards anything real"
        )
        with pytest.raises(EvalDefinitionError, match="duplicate case id"):
            TRANSLATOR.translate(result)

    def test_the_refusal_names_both_places_a_case_is_named(self) -> None:
        """A traced eval never writes an `LLMTestCase(name=...)`, so naming only
        that one sends the reader somewhere they cannot act."""
        result = _evaluate([LLMTestCase(input="a", actual_output="a")] * 2)
        with pytest.raises(EvalDefinitionError) as refusal:
            raise_on_duplicate_case_ids(result)
        message = str(refusal.value)
        assert "LLMTestCase(name=" in message
        assert "update_current_span(name=" in message

    def test_named_cases_are_not_duplicates(self) -> None:
        raise_on_duplicate_case_ids(_evaluate([_case("alpha"), _case("beta")]))

    def test_a_span_scored_on_every_golden_is_refused(self) -> None:
        """One `@observe`d component, two goldens, one name between them."""
        result = _traced_run(
            [_golden("first", question="a"), _golden("second", question="b")],
            component=_unnamed_component,
        )
        spans = [
            case.name
            for case in result.test_results
            if case.name not in {"first", "second"}
        ]
        assert spans == ["_unnamed_component"] * 2, (
            "DeepEval stopped naming a scored span after its own function "
            f"(got {spans}), so the refusal below no longer guards anything real"
        )
        with pytest.raises(EvalDefinitionError, match="duplicate case id"):
            raise_on_duplicate_case_ids(result)

    def test_naming_a_span_gives_it_a_case_of_its_own(self) -> None:
        """`update_current_span(name=...)` has to reach the case id, or the
        refusal points at a fix that does not work."""
        result = _traced_run(
            [_golden("first", question="a"), _golden("second", question="b")],
            component=_named_component,
        )
        raise_on_duplicate_case_ids(result)
        names = {case.name for case in result.test_results}
        assert {"a/component", "b/component"} <= names, names


class TestDuplicateMetricNames:
    """DeepEval keeps a case's metrics as a list, and two of them can report
    under one name: two `GEval` judges named alike, or one metric class
    instantiated twice at different thresholds. Keyed by name, all but one are
    dropped, so a metric that failed can leave the gate and the run entirely.
    """

    def test_two_metrics_sharing_a_name_are_refused(self) -> None:
        """The failing metric comes first, which is the order that used to be
        recorded as a pass."""
        result = _evaluate(
            [_case("c")], metrics=[Scripted(0.1, threshold=0.9), Scripted(1.0)]
        )
        names = [metric.name for metric in result.test_results[0].metrics_data]
        assert names == ["Scripted", "Scripted"], (
            "DeepEval stopped reporting both metrics under their shared name, "
            "so the refusal below no longer guards anything real"
        )
        with pytest.raises(EvalDefinitionError, match="more than one metric named"):
            TRANSLATOR.translate(result)

    def test_the_refusal_names_the_case_and_the_metric(self) -> None:
        result = _evaluate([_case("alpha")], metrics=[Scripted(0.1), Scripted(1.0)])
        with pytest.raises(EvalDefinitionError) as refusal:
            raise_on_duplicate_metric_names(result)
        assert "'alpha'" in str(refusal.value)
        assert "'Scripted'" in str(refusal.value)
        assert "name=" in str(refusal.value)

    def test_metrics_with_distinct_names_are_not_duplicates(self) -> None:
        round_ = _translate(
            [_case("c")], metrics=[Scripted(0.9), Scripted(0.2, name="Other")]
        )
        assert sorted(round_.attempts[0].results) == ["Other", "Scripted"]
