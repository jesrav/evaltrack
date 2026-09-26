"""The stored record of one pytest session that ran evals, and the JSON it is
written as.

A `RunRecord` is an immutable tree. Every test in the session holds the cases of
its eval, and every case holds the attempts at it. Run-level metadata sits on
top.

It stores verdicts, not only what the evals produced. A test keeps the marker
that gated it and the outcome pytest reached. A case and each of its attempts
keep the outcome the gate reached. A result keeps the bar that applied to it.
"""

import importlib.metadata
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    FiniteFloat,
    StrictInt,
)
from pydantic_core import to_json
from ulid import ULID

from evaltrack.core.errors import InvalidIdentifierError, UnsupportedSchemaError
from evaltrack.core.eval_round import AttemptErrorRecord, RoundAttempt, RoundErrorRecord
from evaltrack.core.results import (
    EvaluatorResult,
    RunnerInfo,
)
from evaltrack.core.user_values import UserValue, degrade_undumpable

RUN_SCHEMA_VERSION: Final = 1
"""The shape and meaning of a stored run.

Bump this number only when an older run can no longer be read, and ship a
migration with the bump. A repository can have its own work to do on a bump.
"""


def ensure_run_id(run_id: str) -> str:
    """Check that a run id is a canonical ULID. Raises `InvalidIdentifierError`
    otherwise."""
    try:
        ULID.from_str(run_id)
    except ValueError as exc:
        raise InvalidIdentifierError(
            f"{run_id!r} cannot be used as a run id: a run id is a ULID, 26 "
            "characters of uppercase Crockford base32, for example "
            "01J9Z3QW2KJ5H8VN4TQY7B6MDC"
        ) from exc
    return run_id


def _parse_run_id(value: str) -> str:
    # Pydantic collects a `ValueError` into a `ValidationError`.
    # `InvalidIdentifierError` is not one.
    try:
        return ensure_run_id(value)
    except InvalidIdentifierError as exc:
        raise ValueError(str(exc)) from exc


# `ULID.from_str` accepts only the canonical uppercase spelling, so every stored
# id compares by exact match.
RunId = Annotated[str, AfterValidator(_parse_run_id)]

# `passed`, `failed` and `skipped` mirror pytest. `errored` is a failure that is
# not a failed assertion.
TestOutcome = Literal["passed", "failed", "errored", "skipped", "xfailed", "xpassed"]

# `errored` reached no verdict, so it is neither a pass nor a fail.
AttemptOutcome = Literal["passed", "failed", "errored"]

CaseOutcome = Literal["passed", "failed", "errored"]

FAILING_OUTCOMES: tuple[TestOutcome, ...] = ("failed", "errored")


class AttemptRecord(BaseModel):
    """One attempt at a case.

    Attributes:
        outcome: `errored` when the task or an evaluator raised. The attempt
            reached no verdict.
        task_duration: Seconds the task took, or None.
        output: The task's output. None when the task raised.
        results: What each evaluator returned, keyed by result name, with the
            bar that applied already resolved onto it.
        errors: Everything that raised, each with the evaluator that raised it,
            or no evaluator when the task did. Empty on a clean attempt.
        details: Runner-specific data, stored as produced and never interpreted.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="allow")

    outcome: AttemptOutcome
    task_duration: FiniteFloat | None = None
    output: UserValue = None
    results: dict[str, EvaluatorResult] = {}
    errors: list[AttemptErrorRecord] = []
    details: dict[str, UserValue] = {}


class CaseRecord(BaseModel):
    """Everything recorded for one case in one run.

    `inputs`, `expected_output` and `metadata` describe the case rather than any
    one attempt at it, so they are taken from the first attempt and the rest have
    to agree with it.

    Attributes:
        inputs: What the task was given, as the runner reported it.
        expected_output: What the case expected, or None when it declares none.
        metadata: The case's own metadata, as the runner reported it.
        attempts: Every attempt at the case, earliest first.
        passed_attempts: Clean attempts that passed the gate.
        clean_attempts: Attempts that did not error.
        errored_attempts: Attempts whose task or an evaluator raised.
        outcome: `errored` when any attempt errored. Otherwise every attempt
            must pass when `repeats` is above 1, and one passing attempt is
            enough when it is not.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="allow")

    inputs: UserValue
    expected_output: UserValue = None
    metadata: UserValue = None
    attempts: list[AttemptRecord] = []
    passed_attempts: int = 0
    clean_attempts: int = 0
    errored_attempts: int = 0
    outcome: CaseOutcome = "failed"


class MarkerSettings(BaseModel):
    """evaltrack metadata for a test's recorded eval.

    Attributes:
        score_bars: Per-case score bars, keyed by score name.
        repeats: Attempts each case needed, all of which must pass.
        flake_reruns: The marker's budget of extra rounds while a case was
            still unproven.
        reliability_target: Cross-run pass-rate target for reporting. Never
            fails a test.
        eval_version: Version label to bump whenever anything that could move
            the result changes: the cases, the evaluators, the bars, the judge
            or the system under test. Runs sharing it are the ones compared
            with one another, so a bump starts a case's history over.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    score_bars: dict[str, FiniteFloat] = {}
    repeats: int = 1
    flake_reruns: int = 0
    reliability_target: float | None = None
    eval_version: str | None = None


# Note: pytest would collect a `Test`-prefixed class out of the test file that
# imports it, which ends the session under `filterwarnings = error`. That is why
# this class is not named TestRecord.
class RecordedTest(BaseModel):
    """The recorded eval and pytest outcome for one test.

    Attributes:
        cases: Per-case results keyed by case id.
        errors: What failed outside any single case, every round's in the order
            the rounds ran. A rerun appends to this rather than replacing it, so
            a round that failed is still readable once a later one has run.
        marker: What the marker declared. None when the test recorded no eval.
        runner: Which eval runner produced the eval, at what version.
            None when the test recorded no eval.
        raw_results: The runner's own result objects, one per round. Empty when
            keep_raw_results is false.
        outcome: pytest's outcome for the test, which is the authoritative one.
            It can disagree with every case below it, since a test whose eval
            passed can still fail later in its body.
        docstring: The test function's docstring, when it has one.
        test_file: The test file's path from the nodeid, so relative to
            pytest's rootdir.
        details: Runner-specific data for the eval as a whole.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="allow")

    cases: dict[str, CaseRecord] = {}
    errors: list[RoundErrorRecord] = []
    marker: MarkerSettings | None = None
    runner: RunnerInfo | None = None
    # Opaque on purpose, since a typed field makes a stored run unreadable once the
    # runner's payload changes.
    raw_results: list[UserValue] = []
    outcome: TestOutcome | None = None
    docstring: str | None = None
    test_file: str | None = None
    details: dict[str, UserValue] = {}


class RecordedBy(BaseModel):
    """Versions of the tools that produced the run. The eval runner is recorded per
    test."""

    model_config = ConfigDict(frozen=True, extra="allow")

    evaltrack: str

    @classmethod
    def from_installed(cls) -> "RecordedBy":
        """Read the version from the installed distribution's metadata."""
        return cls(evaltrack=importlib.metadata.version("evaltrack"))


class RunRecord(BaseModel):
    """An immutable recorded run.

    Attributes:
        run_schema_version: `RUN_SCHEMA_VERSION` at record time. Required, so
            bytes without a stamp are treated as corrupt.
        id: ULID assigned at record time, and the storage key.
        created_at: When the run was recorded. Timezone-aware.
        recorded_by: Versions of the tools that produced the run.
        commit: The commit the run was evaluated at, or None when unknown.
        worktree_dirty: None when unknown.
        labels: Arbitrary key-value metadata.
        tests: Everything recorded per test, keyed by pytest nodeid.
    """

    # `ser_json_inf_nan="strings"`, because the default `null` loads back
    # indistinguishable from a real null. Set here because the top-level
    # serializer applies it to the whole tree.
    model_config = ConfigDict(frozen=True, ser_json_inf_nan="strings", extra="allow")

    run_schema_version: int
    id: RunId
    created_at: AwareDatetime
    recorded_by: RecordedBy
    commit: str | None = None
    worktree_dirty: bool | None = None
    labels: dict[str, str] = {}
    tests: dict[str, RecordedTest]


class _SchemaStamp(BaseModel):
    """The two fields the version check reads, so a run at another schema is
    refused before its shape is checked. Extra fields are ignored."""

    # Strict, because JSON `true` is not a version, and `type(True) is int`.
    run_schema_version: StrictInt
    # Untyped, since a later schema may store the id in another shape.
    id: object = None


def parse_run_json(data: bytes) -> RunRecord:
    """Parse stored run JSON. Undeclared fields survive a `dump_run_json` round trip.

    The stamp is checked before the shape. A bumped stamp means a field changed
    meaning, so a clean parse would prove nothing, and a failed one would be a
    false report of corruption.

    Raises:
        UnsupportedSchemaError: when the stamp is not `RUN_SCHEMA_VERSION`.
        pydantic.ValidationError: when the bytes are not a JSON object with an
            integer stamp, or do not parse as a run at `RUN_SCHEMA_VERSION`.
    """
    stamp = _SchemaStamp.model_validate_json(data)
    if stamp.run_schema_version != RUN_SCHEMA_VERSION:
        subject = f"run {stamp.id}" if stamp.id is not None else "this run"
        raise UnsupportedSchemaError(
            f"{subject} was recorded under run schema "
            f"{stamp.run_schema_version}, and this evaltrack reads "
            f"{RUN_SCHEMA_VERSION}. Use the evaltrack that reads it."
        )
    return RunRecord.model_validate_json(data)


def strip_raw_results(run: RunRecord) -> RunRecord:
    """A copy with every test's `raw_results` cleared."""
    return run.model_copy(
        update={
            "tests": {
                name: test.model_copy(update={"raw_results": []})
                for name, test in run.tests.items()
            }
        }
    )


def _repr_or_stand_in(value: object) -> str:
    # The dump's last resort. A `__repr__` that raises must not lose the run.
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


def dump_plain(model: BaseModel, *, include: set[str] | None = None) -> Any:
    """The model as plain dicts. This is the first half of `dump_run_json`.

    The dump makes plain dicts first, so that the degrade in `dump_plain_json`
    also reaches into a user's own model, which validation cannot see into.
    Cut cycles before this point. The dump copies containers, and the back-edge
    of a copy points at the original, so a walk over the copy sees no cycle.

    `warnings=False`, because pydantic warns when a user value does not match
    its declared type, and under `-W error` that warning alone loses the run.
    """
    return model.model_dump(
        mode="python", include=include, fallback=_repr_or_stand_in, warnings=False
    )


def dump_plain_json(plain: Any) -> bytes:
    """Compact JSON of a `dump_plain` result, or of a part of one. This is the
    second half of `dump_run_json`."""
    # Compact, not indented. Indentation was most of a large run's bytes, and a
    # reader parses either.
    return to_json(
        degrade_undumpable(plain),
        inf_nan_mode="strings",
        fallback=_repr_or_stand_in,
    )


def dump_run_json(run: RunRecord) -> bytes:
    """Serialize a run to compact JSON, degrading unserializable user values to
    `repr()`.

    Always dump runs through this function. A run this produces can always be
    loaded again.
    """
    return dump_plain_json(dump_plain(run))


def dump_output_json(attempt: RoundAttempt) -> bytes:
    """One attempt's output, serialized as `dump_run_json` stores it in a run."""
    return dump_plain_json(dump_plain(attempt, include={"output"})["output"])
