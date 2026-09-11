"""Turn a round into a pytest failure.

Every function here sets `__tracebackhide__`. pytest then leaves the function
out of the traceback it prints. The traceback ends at the line in the test that
called `evaltrack.run`. That line is what the reader has to look at.
"""

from collections.abc import Sequence

from evaltrack.core.errors import EvalExecutionError
from evaltrack.core.eval_round import EvalRound, RoundAttempt, group_by_case_id


def _cut_to_one_line(text: str, *, limit: int = 160) -> str:
    """The first line of `text`, made short enough to read inside a sentence."""
    head = text.strip().splitlines()
    if not head:
        return ""
    return head[0] if len(head[0]) <= limit else head[0][: limit - 1] + "…"


def raise_on_round_errors(eval_round: EvalRound) -> None:
    """Raise `EvalExecutionError` when a round or one of its attempts crashed.

    The message names each one, with the first line of its error.
    """
    __tracebackhide__ = True
    tasks: list[str] = []
    per_case: list[str] = []
    for attempt in eval_round.attempts:
        for error in attempt.errors:
            described = _cut_to_one_line(error.message)
            if error.evaluator is None:
                tasks.append(f"{attempt.case_id} ({described})")
            else:
                per_case.append(f"{attempt.case_id}: {error.evaluator} ({described})")
    msgs: list[str] = []
    if tasks:
        msgs.append(f"task failures: {', '.join(tasks)}")
    if eval_round.errors:
        named = ", ".join(
            f"{e.name} ({_cut_to_one_line(e.message)})" for e in eval_round.errors
        )
        msgs.append(f"round-level failures: {named}")
    if per_case:
        msgs.append(f"per-case evaluator failures: {', '.join(per_case)}")
    if msgs:
        raise EvalExecutionError("the round had: " + "; ".join(msgs))


def _describe_attempt_failures(attempt: RoundAttempt) -> list[str]:
    """How one failed result is described.

    A result that missed its bar reports its value and that bar. Every other
    result reports the reason the evaluator gave, since a bare verdict names
    no bar to adjust.
    """
    details: list[str] = []
    for name, result in attempt.results.items():
        if result.verdict is not False:
            continue
        if result.bar is not None and result.is_score:
            details.append(f"{name}={result.value:g} below bar {result.bar:g}")
            continue
        reason = _cut_to_one_line(result.reason or "")
        details.append(f"{name} ({reason})" if reason else name)
    return details


def _describe_case_failure(attempts: Sequence[RoundAttempt]) -> str:
    """Why a case failed, merged across its attempts with duplicates removed."""
    details: dict[str, None] = {}
    for attempt in attempts:
        if attempt.passed:
            continue
        for detail in _describe_attempt_failures(attempt):
            details[detail] = None
    return ", ".join(details)


def assert_cases_passed(attempts: Sequence[RoundAttempt]) -> None:
    """Fail the test unless every case passed.

    A case passes when each of its attempts is clean, and when every result
    that can fail the attempt passed.

    This function takes no bars, because each result already carries the bar
    it is judged against. An attempt that raised fails its case. Those errors
    are reported before this runs, so the message here does not describe them.

    Raises:
        AssertionError: naming each failing case and what it got wrong.
    """
    __tracebackhide__ = True
    why_by_case = {
        case_id: _describe_case_failure(case_attempts)
        for case_id, case_attempts in group_by_case_id(attempts).items()
        if not all(attempt.passed for attempt in case_attempts)
    }
    if why_by_case:
        # pytest's short summary shows only the first line, so it carries the
        # case ids.
        head = f"{len(why_by_case)} failing case(s): {', '.join(why_by_case)}"
        if len(why_by_case) == 1:
            [(name, why)] = why_by_case.items()
            raise AssertionError(f"{head}: {why}" if why else head)
        lines = [head]
        for name, why in why_by_case.items():
            lines.append(f"  {name}: {why}" if why else f"  {name}")
        raise AssertionError("\n".join(lines))
