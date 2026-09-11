"""The pydantic-evals translator.

A case is keyed by the name the runner reports, or by `f"Case {i}"` numbering its
row from 1 when the user named none. Under `repeat=N` the N expansions share a
`source_case_name`, so they fold back into one case.

An assertion is its own verdict and a score is barred. A label is refused,
since evaltrack gates on every result.
"""

import importlib.metadata
from typing import Any

from pydantic_evals.evaluators import EvaluationResult, EvaluatorFailure
from pydantic_evals.reporting import EvaluationReport, ReportCase, ReportCaseFailure

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import (
    AttemptErrorRecord,
    EvalRound,
    RoundAttempt,
    RoundErrorRecord,
)
from evaltrack.core.results import (
    EvaluatorInfo,
    EvaluatorResult,
    RunnerInfo,
)

NAME = "pydantic-evals"

# A task that raised is reported apart from the cases.
_ReportAttempt = ReportCase | ReportCaseFailure


def _case_id(attempt: _ReportAttempt) -> str:
    """The case id: the name the runner reports, with the `repeat` run index
    collapsed."""
    return attempt.source_case_name or attempt.name


def _build_evaluator(result: EvaluationResult[Any]) -> EvaluatorInfo:
    # `result.source` is pydantic-evals' word for the evaluator that produced it.
    return EvaluatorInfo(name=result.source.name, arguments=result.source.arguments)


def _build_details(attempt: "_ReportAttempt | EvaluationReport") -> dict[str, Any]:
    """The trace coordinates. OpenTelemetry writes "no span" as all zeroes, which an
    unconfigured logfire reports."""
    return {
        key: value
        for key, value in (
            ("trace_id", attempt.trace_id),
            ("span_id", attempt.span_id),
        )
        if value and value.strip("0")
    }


def _build_run_details(report: EvaluationReport) -> dict[str, Any]:
    """The trace coordinates and pydantic-evals' own name for the run, plus
    `metadata=`, where a user keeps anything else about it."""
    details = _build_details(report)
    details["experiment"] = report.name
    if report.experiment_metadata:
        details["experiment_metadata"] = report.experiment_metadata
    return details


def _build_error(failure: EvaluatorFailure) -> AttemptErrorRecord:
    return AttemptErrorRecord(message=failure.error_message, evaluator=failure.name)


def _build_results(case: Any) -> dict[str, EvaluatorResult]:
    results: dict[str, EvaluatorResult] = {}
    for name, result in case.assertions.items():
        results[name] = EvaluatorResult(
            value=result.value,
            reason=result.reason,
            evaluator=_build_evaluator(result),
        )
    for name, result in case.scores.items():
        results[name] = EvaluatorResult(
            value=result.value,
            reason=result.reason,
            evaluator=_build_evaluator(result),
        )
    return results


def _build_case_details(case: ReportCase) -> dict[str, Any]:
    """The trace coordinates, plus what `increment_eval_metric` and
    `set_eval_attribute` gathered inside the task (token counts, cost, a model
    id). Nothing here is a judgment, so none of it becomes a result."""
    details = _build_details(case)
    if case.metrics:
        details["metrics"] = case.metrics
    if case.attributes:
        details["attributes"] = case.attributes
    return details


def _translate_case(case: ReportCase) -> RoundAttempt:
    return RoundAttempt(
        case_id=_case_id(case),
        inputs=case.inputs,
        expected_output=case.expected_output,
        metadata=case.metadata,
        output=case.output,
        results=_build_results(case),
        task_duration=case.task_duration,
        errors=[_build_error(f) for f in case.evaluator_failures],
        details=_build_case_details(case),
    )


def _translate_failure(failure: ReportCaseFailure) -> RoundAttempt:
    """A task that raised. The attempt produced nothing and never passes."""
    return RoundAttempt(
        case_id=_case_id(failure),
        inputs=failure.inputs,
        expected_output=failure.expected_output,
        metadata=failure.metadata,
        errors=[AttemptErrorRecord(message=failure.error_message)],
        details=_build_details(failure),
    )


def raise_on_labels(report: EvaluationReport) -> None:
    """Raise if any evaluator answered with a label.

    pydantic-evals collects a `str` result as a label. evaltrack gates on every
    result, and no comparison decides whether one string is better than
    another, so a label has nothing to gate on.
    """
    __tracebackhide__ = True
    labelled = sorted({name for case in report.cases for name in case.labels})
    if labelled:
        raise EvalDefinitionError(
            "evaltrack: " + ", ".join(labelled) + " answered with a label, "
            "and evaltrack records only results it can gate on. Return an "
            "assertion from the evaluator, or a number to bar with score_bars."
        )


def raise_on_duplicate_case_ids(report: EvaluationReport) -> None:
    """Raise if two cases in the report reach one case id.

    pydantic-evals rejects a duplicate explicit name only, so `Case(name="Case
    2")` next to an unnamed case at position 2 yields two cases named `Case
    2`. Recorded as they are, they merge into one case. A repeat expansion
    carries its own `[i/N]` name, so it is never a duplicate.
    """
    __tracebackhide__ = True
    seen: set[str] = set()
    duplicates: list[str] = []
    for attempt in (*report.cases, *report.failures):
        if attempt.name in seen and attempt.name not in duplicates:
            duplicates.append(attempt.name)
        seen.add(attempt.name)
    if duplicates:
        raise EvalDefinitionError(
            "evaltrack: duplicate case id(s): "
            + ", ".join(map(repr, duplicates))
            + ". pydantic-evals names an unnamed case 'Case <position>', which an "
            "explicit Case(name=...) can repeat. Give every case a unique name."
        )


class PydanticEvalsTranslator:
    """Turns `pydantic_evals` reports into evaltrack's model."""

    def translate(self, result: Any) -> EvalRound:
        report: EvaluationReport = result
        raise_on_duplicate_case_ids(report)
        raise_on_labels(report)
        return EvalRound(
            runner=RunnerInfo(
                name=NAME, version=importlib.metadata.version("pydantic-evals")
            ),
            attempts=[
                _translate_case(attempt)
                if isinstance(attempt, ReportCase)
                else _translate_failure(attempt)
                for attempt in (*report.cases, *report.failures)
            ],
            errors=[
                RoundErrorRecord(name=f.name, message=f.error_message)
                for f in report.report_evaluator_failures
            ],
            details=_build_run_details(report),
        )


TRANSLATOR = PydanticEvalsTranslator()
