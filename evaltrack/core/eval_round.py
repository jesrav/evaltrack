"""One round of an eval in runner-neutral terms. Attempts are a flat list, each
tagged with the case it belongs to.

Nothing in this module is stored, except an error record. A run keeps one as it
stands, so it accepts a field it does not declare, the way every stored model
does.
"""

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from evaltrack.core.results import (
    EvaluatorResult,
    RunnerInfo,
)
from evaltrack.core.user_values import UserStr, UserValue


class AttemptErrorRecord(BaseModel):
    """Something that raised while producing or judging one attempt.

    Attributes:
        message: The exception's type and message, without a traceback.
        evaluator: The evaluator that raised, or None when the task raised.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    message: UserStr
    evaluator: str | None = None


class RoundErrorRecord(BaseModel):
    """A failure that belongs to the whole round rather than to one attempt.

    Attributes:
        name: What the runner calls this failure.
        message: The exception's type and message, without a traceback.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    message: UserStr


class RoundAttempt(BaseModel):
    """One attempt at one case.

    Attributes:
        case_id: The case this is an attempt at. Never empty, since it is the
            key attempts are grouped by.
        inputs: What the task was given, as the runner reported it.
        expected_output: What the case expected, or None when it declares none.
        metadata: The case's own metadata, as the runner reported it.
        output: What the task produced. None when the task raised.
        results: What each evaluator returned, keyed by result name.
        task_duration: Seconds the task took, or None.
        errors: What raised while producing or judging this attempt. An attempt
            with any error never passes.
        details: Runner-specific data, stored as produced and never interpreted.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    inputs: UserValue = None
    expected_output: UserValue = None
    metadata: UserValue = None
    output: UserValue = None
    results: dict[str, EvaluatorResult] = {}
    task_duration: FiniteFloat | None = None
    errors: list[AttemptErrorRecord] = []
    details: dict[str, UserValue] = {}

    @property
    def errored(self) -> bool:
        return bool(self.errors)

    @property
    def passed(self) -> bool:
        """Whether the attempt is clean and no result failed.

        A verdict of None is a score the marker's bars have not reached yet.
        The round is refused if one survives them, so by the time anything asks
        this, every result has answered.
        """
        if self.errored:
            return False
        return all(result.verdict is not False for result in self.results.values())


class EvalRound(BaseModel):
    """One batch of cases, put through the runner once. Build one directly to
    record an eval from a runner that has no translator.

    Attributes:
        runner: Which eval runner produced the batch, and at what version.
        attempts: Every attempt in the batch, in the runner's order. Repetitions
            of one case are separate entries sharing a `case_id`.
        errors: What failed outside any single attempt.
        details: Runner-specific data, stored as produced and never interpreted.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    runner: RunnerInfo
    attempts: list[RoundAttempt] = []
    errors: list[RoundErrorRecord] = []
    details: dict[str, UserValue] = {}


def group_by_case_id(
    attempts: Iterable[RoundAttempt],
) -> dict[str, list[RoundAttempt]]:
    """Attempts keyed by `case_id`, keys in first-seen order and each group in the
    order given."""
    groups: dict[str, list[RoundAttempt]] = defaultdict(list)
    for attempt in attempts:
        groups[attempt.case_id].append(attempt)
    return dict(groups)
