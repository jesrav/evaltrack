"""Groups a test's attempts into the cases the run stores.

A round is flat: one attempt per case, or several when the runner repeated
natively, each tagged with its case id. `repeats` and `flake_reruns` add
further rounds. Grouping turns that into one entry per case, holding every
attempt at it:

    round 1: [c1, c2]          {"c1": [attempt, attempt],
    round 2: [c1, c2]     ->    "c2": [attempt, attempt]}

Cases are keyed in first-seen order and each case's attempts stay earliest
first, so a case's history reads in the order it ran.

An attempt's errors are kept as the runner reported them, one entry per thing
that raised. Only the message length is bounded.
"""

from dataclasses import dataclass, field

from evaltrack.core.eval_round import (
    AttemptErrorRecord,
    EvalRound,
    RoundAttempt,
    group_by_case_id,
)
from evaltrack.core.run_record import (
    AttemptOutcome,
    AttemptRecord,
    CaseOutcome,
    CaseRecord,
)


@dataclass
class _CaseAttempts:
    """One case across rounds. Only the first attempt keeps the case-level inputs."""

    first_attempt: RoundAttempt
    attempts: list[AttemptRecord] = field(default_factory=list)


# A huge message (a dumped payload) must not bloat the run. Every error an
# attempt raised is stored, so the cap is per message.
_ERROR_LIMIT = 500


def _bounded(error: AttemptErrorRecord) -> AttemptErrorRecord:
    if len(error.message) <= _ERROR_LIMIT:
        return error
    capped = error.message[: _ERROR_LIMIT - 1] + "…"
    return error.model_copy(update={"message": capped})


def _derive_attempt_outcome(
    attempt: RoundAttempt,
) -> AttemptOutcome:
    if attempt.errored:
        return "errored"
    return "passed" if attempt.passed else "failed"


def _build_case_attempt(
    attempt: RoundAttempt,
) -> AttemptRecord:
    return AttemptRecord(
        outcome=_derive_attempt_outcome(attempt),
        output=attempt.output,
        results=attempt.results,
        task_duration=attempt.task_duration,
        errors=[_bounded(e) for e in attempt.errors],
        details=attempt.details,
    )


def _collect_case_attempts(
    rounds: list[EvalRound],
) -> dict[str, _CaseAttempts]:
    """Every attempt at each case across rounds, earliest first, keyed by case id in
    first-seen order."""
    by_case: dict[str, _CaseAttempts] = {}
    for eval_round in rounds:
        for case_id, attempts in group_by_case_id(eval_round.attempts).items():
            case = by_case.setdefault(case_id, _CaseAttempts(attempts[0]))
            case.attempts.extend(_build_case_attempt(a) for a in attempts)
    return by_case


def _derive_case_outcome(attempts: list[AttemptRecord], *, repeats: int) -> CaseOutcome:
    """A crash outranks both a pass and a failure. Under `repeats` every attempt
    must pass. Without it, one passing attempt passes the case."""
    if any(a.outcome == "errored" for a in attempts):
        return "errored"
    decide = all if repeats > 1 else any
    return "passed" if decide(a.outcome == "passed" for a in attempts) else "failed"


def _build_case_record(case: _CaseAttempts, *, repeats: int) -> CaseRecord:
    attempts = case.attempts
    clean = [a for a in attempts if a.outcome != "errored"]
    first = case.first_attempt
    return CaseRecord(
        inputs=first.inputs,
        expected_output=first.expected_output,
        metadata=first.metadata,
        attempts=attempts,
        passed_attempts=sum(a.outcome == "passed" for a in clean),
        clean_attempts=len(clean),
        errored_attempts=len(attempts) - len(clean),
        outcome=_derive_case_outcome(attempts, repeats=repeats),
    )


def build_case_records(
    rounds: list[EvalRound], *, repeats: int = 1
) -> dict[str, CaseRecord]:
    """Project one test's rounds into per-case records, keyed by case id.

    Above 1, `repeats` requires every attempt to pass. At 1, one passing
    attempt passes the case.
    """
    return {
        case_id: _build_case_record(case, repeats=repeats)
        for case_id, case in _collect_case_attempts(rounds).items()
    }
