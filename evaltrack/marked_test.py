"""What a marked test calls, and the object that records its rounds.

evaltrack translates a round, checks its shape, and records it. It then joins
the round to the rounds before it. The rules a round must satisfy live in
`evaltrack.core`, which also does the joining.

The running `MarkedTest` is kept on the thread, with `threading.local`. A
`ContextVar` does not work here. An async plugin can copy the context before
the value is set, then run the test body in that copy, where the value is
missing.
"""

import inspect
import threading
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import Any, ParamSpec

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import EvalRound, RoundAttempt
from evaltrack.core.gate import (
    raise_on_unknown_result_fields,
    raise_unless_every_result_gates,
)
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.repetition import (
    check_round_shape,
    eval_repeated_the_cases,
    find_failing_case_ids,
    merge_attempts,
    stack_attempts,
)
from evaltrack.core.run_record import MarkerSettings
from evaltrack.core.score_bars import (
    apply_score_bars,
    raise_on_unmatched_score_bars,
)
from evaltrack.core.user_values import RawResult
from evaltrack.failure import assert_cases_passed, raise_on_round_errors
from evaltrack.translators import find_translator

_thread_state = threading.local()

# The eval's own parameters. `run` and `run_async` forward them untouched, so
# a caller's arguments keep their types through evaltrack to the runner.
P = ParamSpec("P")

# --- what a marked test calls ---


def run(evaluate: Callable[P, Any], /, *args: P.args, **kwargs: P.kwargs) -> None:
    """Run an eval through evaltrack, and add rounds as the marker requires.

    Call this from a test marked `@pytest.mark.evaltrack(...)`. Every round is
    recorded, and the test fails when a case fails. `docs/marker.md` says how many
    rounds the marker asks for, and which attempts must pass.

    Args:
        evaluate: Runs the eval and returns whatever the runner produces. It is
            called once per round, so it must run the eval each time, over the
            same dataset. A round that adds, drops or renames a case is refused.
        *args: Passed to `evaluate` on every round.
        **kwargs: Passed to `evaluate` on every round.

    Raises:
        EvalDefinitionError: in any of these cases.
            - No marked test is running.
            - `evaluate` is a result, not something to call.
            - `evaluate` must be awaited.
            - The eval has no cases.
            - A round does not agree with the first round, or with the marker.
        TranslatorNotFoundError: when no registered translator takes what the
            eval returned.
        EvalExecutionError: when a task or evaluator raised. An error is never retried.
        AssertionError: when a case failed the gate.

    For an eval whose entry point is a coroutine, use `run_async`.
    """
    __tracebackhide__ = True
    marked_test = _require_marked_test()
    _require_something_to_call(evaluate, entry_point="evaltrack.run")
    previous = _call_sync(evaluate, *args, **kwargs)
    attempts = marked_test.record(previous).attempts
    if _must_drive_repeats(marked_test.settings, attempts):
        for _ in range(marked_test.settings.repeats - 1):
            result = _call_sync(evaluate, *args, **kwargs)
            _require_a_fresh_result(result, previous)
            previous = result
            round_ = marked_test.record(result, continuing=True)
            attempts = stack_attempts(attempts, round_.attempts)
    else:
        for _ in range(marked_test.settings.flake_reruns):
            if not find_failing_case_ids(attempts):
                break
            result = _call_sync(evaluate, *args, **kwargs)
            _require_a_fresh_result(result, previous)
            previous = result
            round_ = marked_test.record(result, continuing=True)
            attempts = merge_attempts(attempts, round_.attempts)
    assert_cases_passed(attempts)


async def run_async(
    evaluate: Callable[P, Awaitable[Any]], /, *args: P.args, **kwargs: P.kwargs
) -> None:
    """`run` for an eval that must be awaited, such as pydantic-evals'
    `Dataset.evaluate`. Same arguments and exceptions as `run`.

    The round loop below is the same as the loop in `run`, with await added.
    If you change one loop, change the other.
    """
    __tracebackhide__ = True
    marked_test = _require_marked_test()
    _require_something_to_call(evaluate, entry_point="evaltrack.run_async")
    previous = await _call_async(evaluate, *args, **kwargs)
    attempts = marked_test.record(previous).attempts
    if _must_drive_repeats(marked_test.settings, attempts):
        for _ in range(marked_test.settings.repeats - 1):
            result = await _call_async(evaluate, *args, **kwargs)
            _require_a_fresh_result(result, previous)
            previous = result
            round_ = marked_test.record(result, continuing=True)
            attempts = stack_attempts(attempts, round_.attempts)
    else:
        for _ in range(marked_test.settings.flake_reruns):
            if not find_failing_case_ids(attempts):
                break
            result = await _call_async(evaluate, *args, **kwargs)
            _require_a_fresh_result(result, previous)
            previous = result
            round_ = marked_test.record(result, continuing=True)
            attempts = merge_attempts(attempts, round_.attempts)
    assert_cases_passed(attempts)


def repeats() -> int:
    """The attempts per case the marker requires, for an eval that repeats them itself.

    Pass it as pydantic-evals `repeat=`. The eval must then produce exactly
    this many attempts. Returns 1 when the marker requires
    nothing.

    Raises:
        EvalDefinitionError: when no marked test is running.
    """
    __tracebackhide__ = True
    return _require_marked_test().settings.repeats


# --- recording a test's rounds ---


class MarkedTest:
    """One marked test's eval, from the first round to the gate. `settings` is what
    the marker asked for, recorded with the eval."""

    def __init__(
        self,
        nodeid: str,
        *,
        recorder: EvalRecorder,
        settings: MarkerSettings,
    ) -> None:
        self.nodeid = nodeid
        self.recorder = recorder
        self.settings = settings

    def _translate(self, result: Any) -> EvalRound:
        """Build an `EvalRound` from a runner's result, and apply the marker's
        bars. An `EvalRound` passes through."""
        eval_round = (
            result
            if isinstance(result, EvalRound)
            else find_translator(result).translate(result)
        )
        raise_on_unknown_result_fields(eval_round)
        # The only place that applies the marker's bars.
        raise_on_unmatched_score_bars(eval_round, self.settings.score_bars)
        return apply_score_bars(eval_round, self.settings.score_bars)

    def _add(
        self,
        eval_round: EvalRound,
        *,
        raw_result: RawResult = None,
        continuing: bool = False,
    ) -> None:
        """Record one round, then raise on anything that crashed in it."""
        __tracebackhide__ = True
        if not eval_round.attempts:
            # A round that died says why it produced nothing, so report that
            # first. Otherwise the runner's own message is lost and the reader
            # goes to check a dataset that loaded correctly.
            raise_on_round_errors(eval_round)
            raise EvalDefinitionError(
                "evaltrack: the eval has no cases, so nothing was evaluated. "
                "Did the dataset load correctly?"
            )
        self.recorder.add_round(
            self.nodeid,
            eval_round,
            raw_result=raw_result,
            continuing=continuing,
            settings=self.settings,
        )
        # Raised before the reruns and the gate. A crash is the root cause and is
        # never retried.
        raise_on_round_errors(eval_round)

    def record(self, result: Any, *, continuing: bool = False) -> EvalRound:
        """Translate one round, check its shape, and record it. Nothing here fails
        a case."""
        __tracebackhide__ = True
        eval_round = self._translate(result)
        check_round_shape(
            eval_round,
            repeats=self.settings.repeats,
            flake_reruns=self.settings.flake_reruns,
        )
        self._add(eval_round, raw_result=result, continuing=continuing)
        # After the record, so a round the gate refuses is still inspectable,
        # and after the crash report, which is the root cause when both apply.
        raise_unless_every_result_gates(eval_round, self.nodeid)
        return eval_round


@contextmanager
def bind_marked_test(marked_test: MarkedTest | None) -> Generator[None]:
    """Put `marked_test` on this thread, and restore the outer binding on exit.

    One pytest session can then run inside another. A binding that outlived its
    test would record the next test's eval against this one.
    """
    outer = _set_marked_test(marked_test)
    try:
        yield
    finally:
        _set_marked_test(outer)


def _set_marked_test(marked_test: MarkedTest | None) -> MarkedTest | None:
    """Put `marked_test` on this thread, and return the binding it replaced."""
    outer = get_marked_test()
    _thread_state.marked_test = marked_test
    return outer


def get_marked_test() -> MarkedTest | None:
    """The marked test running on this thread, if any."""
    return getattr(_thread_state, "marked_test", None)


def _require_marked_test() -> MarkedTest:
    __tracebackhide__ = True
    marked_test = get_marked_test()
    if marked_test is None:
        raise EvalDefinitionError(
            "evaltrack was called outside a test marked "
            "@pytest.mark.evaltrack, so there is nothing to record against. "
            "Mark the test, and call from the test body itself. evaltrack is "
            "bound to a test only from its setup to its teardown, so an eval "
            "run from a fixture shared with other tests, or from a thread the "
            "test starts, is not covered."
        )
    return marked_test


# --- calling the eval ---


def _require_something_to_call(evaluate: Any, *, entry_point: str) -> None:
    """Refuse a result where the eval itself belongs. The common mistake is
    `run(dataset.evaluate_sync(task))`, which runs the eval before handing it over."""
    __tracebackhide__ = True
    if callable(evaluate):
        return
    if inspect.iscoroutine(evaluate):
        # Closed rather than dropped, for the reason _call_sync gives.
        evaluate.close()
    example = (
        f"await {entry_point}(dataset.evaluate, task)"
        if entry_point == "evaltrack.run_async"
        else f"{entry_point}(dataset.evaluate_sync, task)"
    )
    kind = f"{type(evaluate).__module__}.{type(evaluate).__qualname__}"
    raise EvalDefinitionError(
        f"evaltrack: {entry_point}() was given {kind}, which cannot be called. "
        "evaltrack runs the eval once per round, so pass the eval and its "
        f"arguments instead: {example}."
    )


def _require_a_fresh_result(result: Any, previous: Any) -> None:
    """Compare identity rather than equality, because a flaky eval can fail twice
    the same way."""
    __tracebackhide__ = True
    if result is previous:
        raise EvalDefinitionError(
            "evaltrack: the callable given to evaltrack.run() returned the same "
            "result object again, so it did not re-run the eval. It is called "
            "once per round, so it has to run the eval each time: pass the eval "
            "itself, `evaltrack.run(dataset.evaluate_sync, task)`, not "
            "something that hands back a result it already has."
        )


def _call_sync(evaluate: Callable[P, Any], /, *args: P.args, **kwargs: P.kwargs) -> Any:
    """Run one round, and refuse an awaitable result.

    evaltrack closes the coroutine instead of dropping it. Python warns about
    a coroutine that nobody awaited, at the moment it frees the object. That
    moment can arrive during another test, and under `filterwarnings = error`
    the warning fails that test instead of this one.
    """
    __tracebackhide__ = True
    result: Any = evaluate(*args, **kwargs)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise EvalDefinitionError(
            "evaltrack: the eval given to evaltrack.run() returned something "
            "to await, which it cannot. Use `await "
            "evaltrack.run_async(dataset.evaluate, task)` instead."
        )
    return result


async def _call_async(
    evaluate: Callable[P, Awaitable[Any]], /, *args: P.args, **kwargs: P.kwargs
) -> Any:
    """Await one round, and refuse a result that cannot be awaited."""
    __tracebackhide__ = True
    result: Any = evaluate(*args, **kwargs)
    if not inspect.isawaitable(result):
        raise EvalDefinitionError(
            "evaltrack: the eval given to evaltrack.run_async() returned "
            f"{type(result).__module__}.{type(result).__qualname__}, which "
            "cannot be awaited. A synchronous eval goes to "
            "`evaltrack.run(dataset.evaluate_sync, task)`."
        )
    return await result


def _must_drive_repeats(settings: MarkerSettings, attempts: list[RoundAttempt]) -> bool:
    """Whether evaltrack must run the remaining rounds itself.

    The marker's `repeats=` is satisfied from one of two directions. The eval
    can repeat the cases itself, and the first round then already holds every
    attempt. Otherwise it gives one attempt per case, and evaltrack calls it
    again for each round still owed.
    """
    return settings.repeats > 1 and not eval_repeated_the_cases(attempts)
