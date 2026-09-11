"""What the marker's repetition settings mean for a case.

The attempts of a new round join the ones already recorded in one of two ways.
The marker decides which one:

- `repeats=N` demands consistency. The eval runs N times, and every attempt
  must pass. A stack adds each round's attempts to the list, so the gate sees
  all of them.
- `flake_reruns=N` absorbs flakiness. The eval runs again while a case is still
  failing, and one pass settles that case. A merge replaces only the attempts
  that failed, so a case that has passed keeps its attempt. A later round
  cannot take that pass away.

The gate does not change. It asks the same question both times: whether every
attempt in the list passed.
"""

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import EvalRound, RoundAttempt, group_by_case_id


def find_failing_case_ids(attempts: list[RoundAttempt]) -> list[str]:
    """Ids of the cases that did not pass, in the order they first appear."""
    return [
        case_id
        for case_id, case_attempts in group_by_case_id(attempts).items()
        if not all(attempt.passed for attempt in case_attempts)
    ]


def eval_repeated_the_cases(attempts: list[RoundAttempt]) -> bool:
    """Whether one round holds more than one attempt per case, however the eval
    produced them — usually a runner's own `repeat=`. `check_round_shape` has
    already refused a round whose cases disagree, so one count answers for all
    of them."""
    counts = [len(a) for a in group_by_case_id(attempts).values()]
    return max(counts, default=1) > 1


def _raise_on_drifted_ids(
    attempts_so_far: list[RoundAttempt], round_attempts: list[RoundAttempt]
) -> None:
    """Refuse a round whose case ids are not the ids of the first round.

    A new id matches no case in the list. evaltrack still records that case,
    but the case can never fail the test. A missing id means the dataset
    changed while the eval ran.
    """
    __tracebackhide__ = True
    known = {attempt.case_id for attempt in attempts_so_far}
    current = {attempt.case_id for attempt in round_attempts}
    unplaceable = sorted(current - known)
    missing = sorted(known - current)
    if not unplaceable and not missing:
        return
    parts: list[str] = []
    if unplaceable:
        parts.append(
            "produced case ids the first round did not: " + ", ".join(unplaceable)
        )
    if missing:
        parts.append("was missing the first round's ids " + ", ".join(missing))
    raise EvalDefinitionError(
        f"evaltrack: a further round {', and '.join(parts)}. The callable "
        "given to evaltrack.run() is called once per round and must evaluate "
        "the same dataset each time. Give cases stable names and make the "
        "dataset deterministic."
    )


def stack_attempts(
    attempts_so_far: list[RoundAttempt], round_attempts: list[RoundAttempt]
) -> list[RoundAttempt]:
    """Add the attempts of one more round to the list.

    Every case must appear in every round.

    Raises:
        EvalDefinitionError: when the case ids of the round are not the ids
            of the first round.
    """
    _raise_on_drifted_ids(attempts_so_far, round_attempts)
    return [*attempts_so_far, *round_attempts]


def merge_attempts(
    attempts_so_far: list[RoundAttempt], round_attempts: list[RoundAttempt]
) -> list[RoundAttempt]:
    """Replace each failed attempt with the attempt from the new round.

    A case that already passed keeps its attempt. Each round has one attempt
    per case.

    Raises:
        EvalDefinitionError: when the case ids of the round are not the ids
            of the first round.
    """
    _raise_on_drifted_ids(attempts_so_far, round_attempts)
    from_round = {attempt.case_id: attempt for attempt in round_attempts}
    return [
        attempt if attempt.passed else from_round[attempt.case_id]
        for attempt in attempts_so_far
    ]


def check_round_shape(
    eval_round: EvalRound, *, repeats: int, flake_reruns: int
) -> None:
    """Refuse a round whose repetition disagrees with the marker.

    Each case has one attempt. A case has exactly `repeats` attempts when the
    eval repeated the cases itself.
    """
    __tracebackhide__ = True
    counts = {
        case_id: len(attempts)
        for case_id, attempts in group_by_case_id(eval_round.attempts).items()
    }
    distinct = sorted(set(counts.values()))
    if len(distinct) > 1:
        described = ", ".join(
            f"{case_id} x{count}" for case_id, count in sorted(counts.items())
        )
        raise EvalDefinitionError(
            f"evaltrack: the eval repeated some cases and not others "
            f"({described}). Repetition is declared per test with repeats= on "
            "the marker, so every case must have the same number of attempts."
        )
    count = distinct[0] if distinct else 1
    if count == 1:
        return
    if flake_reruns:
        raise EvalDefinitionError(
            f"evaltrack: the eval repeated each case {count} times, and the "
            "marker sets flake_reruns=. The two pull in opposite directions: "
            "a native repeat demands every attempt pass, a flake rerun lets "
            "one pass settle the case. Use repeats= for consistency, or drop "
            "the native repetition to keep flake_reruns=."
        )
    if repeats <= 1:
        raise EvalDefinitionError(
            f"evaltrack: the eval repeated each case {count} times, and the "
            f"marker demands nothing. Declare repeats={count} on the marker so "
            "the gate says what it requires, or drop the repetition "
            "(pydantic-evals repeat=)."
        )
    if count != repeats:
        raise EvalDefinitionError(
            f"evaltrack: the eval repeated each case {count} times, and the "
            f"marker demands repeats={repeats}. Make them agree: the marker is "
            "what the gate enforces, the eval is what produced the attempts."
        )
