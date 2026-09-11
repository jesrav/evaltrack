"""The DeepEval translator.

A metric's threshold becomes the bar, so a metric that sets a threshold gates
the case on its own. One that sets none needs a bar from the marker's
`score_bars`, since evaltrack gates on every result.

A metric whose own verdict contradicts `score >= threshold` is refused, since
evaltrack reads the threshold as a bar and cannot tell which answer is right.

A metric that sets `flaky=True` is refused, since DeepEval does not let one
decide its case and evaltrack gates on every result.

A metric that raised, or that produced no usable score, is an error on the
attempt rather than a result.

`evaluate()` runs each case once, so a round holds one attempt per case.
"""

import importlib.metadata
import math
from collections.abc import Iterable
from typing import Any

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import (
    AttemptErrorRecord,
    EvalRound,
    RoundAttempt,
)
from evaltrack.core.results import (
    EvaluatorInfo,
    EvaluatorResult,
    RunnerInfo,
)

NAME = "deepeval"


def _case_id(result: Any, *, position: int) -> str:
    """The name DeepEval reports, falling back to the row so an id is never empty."""
    return getattr(result, "name", None) or f"test_case_{position}"


def _build_evaluator(metric: Any) -> EvaluatorInfo:
    # Strict mode rescores the metric as an assertion at a threshold of 1, so
    # it changes what the number means. Nothing else DeepEval reports is a
    # setting rather than an answer.
    arguments = {"strict_mode": True} if getattr(metric, "strict_mode", False) else None
    return EvaluatorInfo(name=metric.name, arguments=arguments)


def _build_result_details(metric: Any) -> dict[str, Any]:
    """What the judgment cost and what produced it, plus the judge's own trace."""
    provenance = (
        ("evaluation_model", getattr(metric, "evaluation_model", None)),
        ("evaluation_cost", getattr(metric, "evaluation_cost", None)),
        ("input_tokens", getattr(metric, "input_tokens", None)),
        ("output_tokens", getattr(metric, "output_tokens", None)),
        ("verbose_logs", getattr(metric, "verbose_logs", None)),
    )
    return {key: value for key, value in provenance if value is not None}


def _unscored_error(metric: Any, *, score: Any) -> AttemptErrorRecord:
    return AttemptErrorRecord(
        evaluator=metric.name,
        message=(
            f"the metric reported no usable score for this case: {score!r}. "
            "DeepEval leaves the score unset when the metric computed none, "
            "and a case it did not grade has nothing to gate on."
        ),
    )


def _raise_on_a_flaky_metric(metric: Any) -> None:
    """Raise if the metric sets `flaky=True`.

    DeepEval does not let such a metric decide its case, so its threshold is
    not a bar and nothing else about it is a verdict. evaltrack gates on every
    result.
    """
    __tracebackhide__ = True
    if not getattr(metric, "flaky", False):
        return
    raise EvalDefinitionError(
        f"evaltrack: the metric {metric.name!r} sets flaky=True, and evaltrack "
        "records only results it can gate on. DeepEval does not let such a "
        "metric decide its case, so its threshold is not a bar. Unset flaky to "
        "gate on the metric, or drop it from this eval."
    )


def _raise_on_a_verdict_that_contradicts_the_threshold(
    metric: Any, *, score: float
) -> None:
    """Raise if DeepEval's verdict disagrees with `score >= threshold`.

    A metric can override `is_successful()` to decide on something else, as a
    lower-is-better one does. evaltrack reads the threshold as a bar, so
    recording it takes the opposite of DeepEval's verdict and a failure lands
    as a pass.
    """
    __tracebackhide__ = True
    if metric.success is None or metric.threshold is None:
        return
    if bool(metric.success) == (score >= metric.threshold):
        return
    raise EvalDefinitionError(
        f"evaltrack: the metric {metric.name!r} scored {score} against a "
        f"threshold of {metric.threshold}, and DeepEval set "
        f"success={metric.success}. evaltrack passes a score that reaches its "
        "bar, so recording it takes the opposite of DeepEval's verdict. It "
        "cannot tell which answer is right. Make is_successful() call a score "
        "at or above the threshold a success, or stop recording this metric."
    )


def _translate_metric(metric: Any) -> EvaluatorResult | AttemptErrorRecord:
    """One `MetricData` as its result, or as the error that it judged nothing."""
    if metric.error is not None:
        return AttemptErrorRecord(evaluator=metric.name, message=metric.error)
    score = metric.score
    if not isinstance(score, (int, float)) or not math.isfinite(score):
        return _unscored_error(metric, score=score)
    # Before the check below, which a metric with flaky=True is entitled to fail.
    _raise_on_a_flaky_metric(metric)
    _raise_on_a_verdict_that_contradicts_the_threshold(metric, score=score)
    return EvaluatorResult(
        value=score,
        runner_bar=metric.threshold,
        reason=metric.reason,
        evaluator=_build_evaluator(metric),
        details=_build_result_details(metric),
    )


def _build_case_details(result: Any) -> dict[str, Any]:
    """The retrieval material and the conversation. A RAG or multi-turn case
    cannot be read without them. None of it is a judgment, so none of it is a
    result."""
    carried = (
        ("context", getattr(result, "context", None)),
        ("retrieval_context", getattr(result, "retrieval_context", None)),
        ("turns", getattr(result, "turns", None)),
    )
    return {key: value for key, value in carried if value}


def _translate_case(result: Any, *, position: int) -> RoundAttempt:
    results: dict[str, EvaluatorResult] = {}
    errors: list[AttemptErrorRecord] = []
    for metric in getattr(result, "metrics_data", None) or []:
        translated = _translate_metric(metric)
        if isinstance(translated, AttemptErrorRecord):
            errors.append(translated)
        else:
            results[metric.name] = translated
    return RoundAttempt(
        case_id=_case_id(result, position=position),
        inputs=getattr(result, "input", None),
        expected_output=getattr(result, "expected_output", None),
        metadata=getattr(result, "metadata", None),
        output=getattr(result, "actual_output", None),
        results=results,
        errors=errors,
        details=_build_case_details(result),
    )


def _build_run_details(result: Any) -> dict[str, Any]:
    """DeepEval's own coordinates for the run, when it kept any."""
    coordinates = (
        ("test_run_id", getattr(result, "test_run_id", None)),
        ("confident_link", getattr(result, "confident_link", None)),
    )
    return {key: value for key, value in coordinates if value}


def _find_duplicates(names: Iterable[str]) -> list[str]:
    """The names given more than once, in the order they first repeat."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def raise_on_duplicate_case_ids(result: Any) -> None:
    """Raise if two cases in the round reach one case id.

    A case arrives unnamed from two directions. Under pytest, DeepEval names an
    unnamed `LLMTestCase` after the running test. On a traced run it names a
    scored span after the function it decorates. Either way a whole dataset
    arrives under one name, and recording it merges the cases into one with a
    single pass-rate history.
    """
    __tracebackhide__ = True
    duplicates = _find_duplicates(
        _case_id(case, position=position)
        for position, case in enumerate(result.test_results)
    )
    if not duplicates:
        return
    raise EvalDefinitionError(
        "evaltrack: duplicate case id(s): "
        + ", ".join(map(repr, duplicates))
        + ". The id keys the case's pass-rate history across runs, so two "
        "cases sharing one merge into a row no later read can separate. Name "
        "the case where it is built. A case you hand to evaluate() takes "
        "LLMTestCase(name=...), and a span scored on a traced run takes "
        "update_current_span(name=...). Pick a name that outlives the "
        "dataset's order."
    )


def raise_on_duplicate_metric_names(result: Any) -> None:
    """Raise if two metrics on one case report under one name.

    DeepEval keeps a case's metrics as a list and lets them share a name: two
    GEval judges named alike, or one metric class instantiated twice at
    different thresholds. Keyed by name, the last one silently replaces the
    rest, so a metric that failed can vanish from the gate and from the run.
    """
    __tracebackhide__ = True
    for position, case in enumerate(result.test_results):
        metrics = getattr(case, "metrics_data", None) or []
        duplicates = _find_duplicates(metric.name for metric in metrics)
        if duplicates:
            raise EvalDefinitionError(
                "evaltrack: case "
                + repr(_case_id(case, position=position))
                + " reports more than one metric named "
                + ", ".join(map(repr, duplicates))
                + ". A result is keyed by name, so all but one would be "
                "dropped and whatever it judged would go ungated. Give each "
                "metric a distinct name=."
            )


class DeepEvalTranslator:
    """Turns a DeepEval `EvaluationResult` into evaltrack's model."""

    def translate(self, result: Any) -> EvalRound:
        raise_on_duplicate_case_ids(result)
        raise_on_duplicate_metric_names(result)
        return EvalRound(
            runner=RunnerInfo(
                name=NAME, version=importlib.metadata.version("deepeval")
            ),
            attempts=[
                _translate_case(case, position=position)
                for position, case in enumerate(result.test_results)
            ],
            details=_build_run_details(result),
        )


TRANSLATOR = DeepEvalTranslator()
