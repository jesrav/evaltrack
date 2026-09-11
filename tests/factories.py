"""Builders for the eval rounds, stored runs, pydantic-evals reports and
recorded values that tests feed in.

The `*_round` builders produce evaltrack's own runner-neutral model, for a
test of the gate, the recorder or the projection. The `*_run` builders produce
the stored `RunRecord`, for a test of the repository, a cross-run view or the
dashboard. The `*_report` builders produce pydantic-evals reports, for a test of
that translator.

Kept out of `conftest.py` so that file holds fixtures only. These are plain
functions. A test imports them instead of taking them as a fixture.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic_evals import Case, Dataset, increment_eval_metric
from pydantic_evals.evaluators import (
    EvaluationResult,
    Evaluator,
    EvaluatorContext,
    EvaluatorSpec,
)
from pydantic_evals.evaluators.evaluator import EvaluatorFailure
from pydantic_evals.reporting import EvaluationReport, ReportCase, ReportCaseFailure
from ulid import ULID

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
from evaltrack.core.run_record import (
    RUN_SCHEMA_VERSION,
    AttemptOutcome,
    AttemptRecord,
    CaseOutcome,
    CaseRecord,
    MarkerSettings,
    RecordedBy,
    RecordedTest,
    RunRecord,
)
from evaltrack.translators.pydantic_evals import TRANSLATOR

# A pinned version for a hand-built `RunRecord`, whose `recorded_by` is required.
# A fixed value keeps every construction site off the installed version, which
# changes with any release.
RECORDED_BY = RecordedBy(evaltrack="0.0.0")

# The runner a neutral fixture claims to come from. Nothing after the translator
# reads it, so the value does not matter.
FAKE_RUNNER = RunnerInfo(name="fake-runner", version="0.0.0")


def read_case_ids(eval_round: EvalRound) -> list[str]:
    """Every case in the batch, in first-seen order, without repetitions."""
    return list(dict.fromkeys(attempt.case_id for attempt in eval_round.attempts))


def make_attempt(
    case_id: str = "test_case",
    *,
    scores: dict[str, float] | None = None,
    assertions: dict[str, bool] | None = None,
    reasons: dict[str, str | None] | None = None,
    thresholds: dict[str, float] | None = None,
    errors: list[AttemptErrorRecord] | None = None,
    inputs: Any = "in",
    expected_output: Any = None,
    metadata: Any = None,
    output: Any = "out",
    task_duration: float | None = 1.0,
    details: dict[str, Any] | None = None,
) -> RoundAttempt:
    """One attempt at `case_id`, carrying the given results.

    `reasons` replaces a result's reason by name, None included, so a test can
    exercise both the reasoned and the bare evaluator. `thresholds` puts the
    runner's own bar on a score.
    """
    reasons = reasons or {}
    thresholds = thresholds or {}
    source = EvaluatorInfo(name="test_evaluator")
    return RoundAttempt(
        case_id=case_id,
        inputs=inputs,
        expected_output=expected_output,
        metadata=metadata,
        output=output,
        results={
            **{
                k: EvaluatorResult(
                    value=v,
                    runner_bar=thresholds.get(k),
                    reason=reasons.get(k, "t"),
                    evaluator=source,
                )
                for k, v in (scores or {}).items()
            },
            **{
                k: EvaluatorResult(
                    value=v, reason=reasons.get(k, "t"), evaluator=source
                )
                for k, v in (assertions or {}).items()
            },
        },
        task_duration=task_duration,
        errors=errors or [],
        details=details or {},
    )


def make_round(
    *,
    attempts: list[RoundAttempt] | None = None,
    scores: dict[str, float] | None = None,
    assertions: dict[str, bool] | None = None,
    task_duration: float | None = 1.0,
    errors: list[RoundErrorRecord] | None = None,
    details: dict[str, Any] | None = None,
) -> EvalRound:
    """An `EvalRound`, by default holding one passing case named `test_case`.

    Pass `attempts` to set the cases yourself. The `scores` and `assertions`
    shorthands build that single default case instead.
    """
    if attempts is None:
        attempts = [
            make_attempt(
                scores=scores if scores is not None else {"quality": 0.85},
                # The default score carries the runner's bar, so the default
                # round is one the gate can read in full.
                thresholds=None if scores is not None else {"quality": 0.5},
                assertions=assertions if assertions is not None else {"passed": True},
                task_duration=task_duration,
                inputs="test input",
                output="test output",
            )
        ]
    return EvalRound(
        runner=FAKE_RUNNER,
        attempts=attempts,
        errors=errors or [],
        details=details or {},
    )


def make_crash_round(
    *,
    case_id: str = "test_case",
    error_message: str = "RuntimeError: boom",
) -> EvalRound:
    """A round whose one case never produced a result, because the task raised."""
    return make_round(
        attempts=[
            make_attempt(
                case_id,
                inputs="test input",
                output=None,
                task_duration=None,
                errors=[AttemptErrorRecord(message=error_message)],
            )
        ],
    )


def make_repeat_round(
    passes: list[bool],
    *,
    case_id: str = "c",
    scores: dict[str, float] | None = None,
) -> EvalRound:
    """A round holding `len(passes)` attempts at one case.

    The boolean at position i decides whether attempt i's `passed` assertion is
    True. Every attempt shares a case id, so they are repeats of one case rather
    than several cases.
    """
    return make_round(
        attempts=[
            make_attempt(
                case_id,
                assertions={"passed": ok},
                scores=scores,
                output=f"output {i}",
            )
            for i, ok in enumerate(passes, 1)
        ],
    )


def translate_report(report: EvaluationReport) -> EvalRound:
    """A real pydantic-evals report in evaltrack's model.

    For a test that needs what the runner really produces (a crash mid-repeat, a
    value that does not serialize) rather than a hand-built round.
    """
    return TRANSLATOR.translate(report)


# One character per attempt, in the order they ran. A case's attempts are what the
# cross-run views pool, and the spelled-out string is shorter than the counts it
# replaces. It also cannot contradict itself, because the rollups below are
# derived from it.
_OUTCOMES: dict[str, AttemptOutcome] = {
    "P": "passed",
    "F": "failed",
    "E": "errored",
}


def make_case_result(
    attempts: str,
    *,
    inputs: Any = None,
    expected_output: Any = None,
    metadata: Any = None,
) -> CaseRecord:
    """A `CaseRecord` whose attempts ran as `attempts` spells them: `P`assed,
    `F`ailed, `E`rrored.

    The rolled-up counts are derived rather than passed in, so they agree with
    the attempts the way the recorder's do. A test that needs them to disagree
    builds the `CaseRecord` itself.
    """
    outcomes: list[AttemptOutcome] = [_OUTCOMES[c] for c in attempts]
    clean: list[AttemptOutcome] = [o for o in outcomes if o != "errored"]
    errors = len(outcomes) - len(clean)
    rolled_up: CaseOutcome
    if errors:
        rolled_up = "errored"  # a crash outranks any verdict the case reached
    elif clean and all(o == "passed" for o in clean):
        rolled_up = "passed"
    else:
        rolled_up = "failed"
    return CaseRecord(
        inputs=inputs,
        expected_output=expected_output,
        metadata=metadata,
        attempts=[AttemptRecord(outcome=o, task_duration=0.0) for o in outcomes],
        passed_attempts=clean.count("passed"),
        clean_attempts=len(clean),
        errored_attempts=errors,
        outcome=rolled_up,
    )


def make_eval_run(
    *,
    run_id: str | None = None,
    created_at: datetime | str | None = None,
    commit: str | None = None,
    worktree_dirty: bool | None = None,
    labels: dict[str, str] | None = None,
    tests: dict[str, RecordedTest] | None = None,
) -> RunRecord:
    """A stored `RunRecord`, by default holding no tests.

    Saves every call site the four-field preamble that a run always carries.
    `created_at` takes an ISO 8601 string as well as a datetime, so a test that
    dates several runs stays compact.
    """
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    return RunRecord(
        run_schema_version=RUN_SCHEMA_VERSION,
        id=run_id or str(ULID()),
        created_at=created_at or datetime.now(UTC),
        recorded_by=RECORDED_BY,
        commit=commit,
        worktree_dirty=worktree_dirty,
        labels=labels or {},
        tests=tests if tests is not None else {},
    )


def make_case_run(
    label: str,
    created_at: str,
    attempts: str,
    *,
    nodeid: str = "t",
    case: str = "c",
    inputs: Any = None,
    eval_version: str | None = None,
    reliability_target: float | None = None,
    commit: str | None = None,
) -> RunRecord:
    """A run whose one test recorded one case with the given attempts.

    The shape every cross-run view reads: reliability and the score trend pool a
    case across runs, so a test of either needs several of these.
    """
    return make_eval_run(
        run_id=make_run_id(label),
        created_at=created_at,
        commit=commit,
        tests={
            nodeid: RecordedTest(
                cases={case: make_case_result(attempts, inputs=inputs)},
                marker=MarkerSettings(
                    eval_version=eval_version,
                    reliability_target=reliability_target,
                ),
            )
        },
    )


def make_run_id(label: str) -> str:
    """A run id that is a valid ULID and still names the run when a test fails.

    Tests that order runs compare ids, so the label keeps its sort position. It
    sits behind a fixed `01` prefix, because a ULID's first ten characters are a
    timestamp that must not overflow. The label is uppercased and zero padded,
    because a ULID holds 26 characters of uppercase Crockford base32.
    """
    return f"01{label.upper():0<24}"


class ReprOnlyValue:
    """User data with no JSON form, so only its `repr` can be recorded."""

    def __repr__(self) -> str:
        return "ReprOnlyValue()"


class _Passes(Evaluator[Any, Any, Any]):
    def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> bool:
        return True


async def _awkward_task(inputs: str) -> dict[str, Any]:
    increment_eval_metric("tokens", float("inf"))
    nested: Any = "leaf"
    for _ in range(20):
        nested = {"child": [nested]}
    return {"ratio": float("nan"), "opaque": ReprOnlyValue(), "nested": nested}


def evaluate_awkward_report() -> EvaluationReport:
    """Report from a real `Dataset.evaluate` over a task whose values are awkward
    to store:

    - a non-finite metric,
    - a non-finite float and a repr-only object in the output,
    - deep nesting,
    - repr-only case metadata.

    `make_eval_report` is clean by construction, so only a real eval shows what
    pydantic-evals puts in a report.
    """
    dataset = Dataset[str, Any, Any](
        name="awkward",
        cases=[Case(name="awkward_case", inputs="x", metadata=ReprOnlyValue())],
        evaluators=[_Passes()],
    )
    return asyncio.run(dataset.evaluate(_awkward_task))


def make_report_case(
    name: str,
    scores: dict[str, float] | None = None,
    assertions: dict[str, bool] | None = None,
    reasons: dict[str, str | None] | None = None,
) -> ReportCase:
    """Build a single `ReportCase` carrying the given scores and assertions.

    `reasons` replaces a result's reason by name, None included, so a test can
    exercise both the reasoned and the bare evaluator.
    """
    reasons = reasons or {}
    spec = EvaluatorSpec(name="test", arguments=None)
    return ReportCase(
        name=name,
        inputs="in",
        metadata={},
        expected_output=None,
        output="out",
        metrics={},
        attributes={},
        scores={
            k: EvaluationResult(
                name=k, value=v, reason=reasons.get(k, "t"), source=spec
            )
            for k, v in (scores or {}).items()
        },
        labels={},
        assertions={
            k: EvaluationResult(
                name=k, value=v, reason=reasons.get(k, "t"), source=spec
            )
            for k, v in (assertions or {}).items()
        },
        task_duration=1.0,
        total_duration=1.0,
    )


def make_evaluator_failure(name: str = "MyEvaluator") -> EvaluatorFailure:
    """An evaluator that crashed while scoring a case."""
    return EvaluatorFailure(
        name=name,
        error_message="oops",
        error_stacktrace="...",
        source=EvaluatorSpec(name=name, arguments=None),
        error_type="ValueError",
    )


def make_eval_report(
    *,
    name: str = "test_eval",
    scores: dict[str, float] | None = None,
    assertions: dict[str, bool] | None = None,
    labels: dict[str, str] | None = None,
    task_duration: float = 1.0,
) -> EvaluationReport:
    """Build an `EvaluationReport` with one case carrying the given results.

    Saves a test from building the pydantic-evals report skeleton by hand.
    """
    scores_dict = scores or {"quality": 0.85}
    assertions_dict = assertions or {"passed": True}
    labels_dict = labels or {}

    spec = EvaluatorSpec(name="test_evaluator", arguments=None)
    case = ReportCase(
        name="test_case",
        inputs="test input",
        metadata={},
        expected_output=None,
        output="test output",
        metrics={},
        attributes={},
        scores={
            k: EvaluationResult(name=k, value=v, reason="test", source=spec)
            for k, v in scores_dict.items()
        },
        labels={
            k: EvaluationResult(name=k, value=v, reason="test", source=spec)
            for k, v in labels_dict.items()
        },
        assertions={
            k: EvaluationResult(name=k, value=v, reason="test", source=spec)
            for k, v in assertions_dict.items()
        },
        task_duration=task_duration,
        total_duration=task_duration + 1.0,
    )
    return EvaluationReport(name=name, cases=[case])


def make_crash_report(
    *,
    name: str = "crash_eval",
    case_name: str = "test_case",
    error_message: str = "RuntimeError: boom",
) -> EvaluationReport:
    """Build a report for one case whose task raised, so the report holds no
    cases and one failure.

    Mirrors how pydantic-evals reports a task exception, on `report.failures`
    rather than as a case.
    """
    failure = ReportCaseFailure(
        name=case_name,
        inputs="test input",
        metadata={},
        expected_output=None,
        error_message=error_message,
        error_stacktrace=f"Traceback (most recent call last):\n  {error_message}",
    )
    return EvaluationReport(name=name, cases=[], failures=[failure])


def make_repeat_report(
    passes: list[bool],
    *,
    name: str = "rep_eval",
    case_name: str = "c",
    scores: dict[str, float] | None = None,
) -> EvaluationReport:
    """Build a report for one case run `len(passes)` times under `repeat=N`.

    Mirrors how pydantic-evals names repeated cases. Each one gets a distinct
    ``"{case} [i/N]"`` name and shares `source_case_name == case_name`, so they
    collapse back into one case with N attempts. The boolean at position i
    decides whether attempt i's `passed` assertion is True.
    """
    spec = EvaluatorSpec(name="test_evaluator", arguments=None)
    n = len(passes)
    cases = [
        ReportCase(
            name=f"{case_name} [{i}/{n}]",
            source_case_name=case_name,
            inputs="test input",
            metadata={},
            expected_output=None,
            output=f"output {i}",
            metrics={},
            attributes={},
            scores={
                k: EvaluationResult(name=k, value=v, reason="test", source=spec)
                for k, v in (scores or {}).items()
            },
            labels={},
            assertions={
                "passed": EvaluationResult(
                    name="passed", value=ok, reason="test", source=spec
                )
            },
            task_duration=1.0,
            total_duration=2.0,
        )
        for i, ok in enumerate(passes, 1)
    ]
    return EvaluationReport(name=name, cases=cases)
