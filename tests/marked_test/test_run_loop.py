"""The loop `evaltrack.run` drives: `flake_reruns=` and `repeats=`.

`run` takes a way to produce a result rather than a result, so evaltrack can
run a runner it does not drive itself again. These pin when it does, how many
times, and when it stops.

The callables here return `EvalRound`s directly, which `run` accepts as-is,
so nothing below is about a runner. What each round records is
`test_api.py`, and how rounds fold together is `test_merge.py`.
"""

import asyncio
import gc
import warnings
from typing import Any

import pytest

from evaltrack import repeats, run, run_async
from evaltrack.core.errors import EvalDefinitionError, EvalExecutionError
from evaltrack.core.eval_round import AttemptErrorRecord, EvalRound
from evaltrack.marked_test import MarkedTest

from ..factories import make_attempt, make_round
from .conftest import TEST_NODEID, Bind


@pytest.fixture
def marked_test(bind: Bind) -> MarkedTest:
    """A `MarkedTest` with a rerun budget, which is what this module is about."""
    return bind(flake_reruns=3)


def _verdicts(*rounds: dict[str, bool]) -> Any:
    """A callable returning one eval result per round, cases keyed by verdict."""
    remaining = list(rounds)

    def evaluate() -> EvalRound:
        verdicts = remaining.pop(0) if remaining else rounds[-1]
        return make_round(
            attempts=[
                make_attempt(case_id, assertions={"ok": passed})
                for case_id, passed in verdicts.items()
            ]
        )

    return evaluate


def _recorded_attempts(marked_test: MarkedTest, case_id: str) -> int:
    run_ = marked_test.recorder.to_run_record()
    return len(run_.tests[TEST_NODEID].cases[case_id].attempts)


def test_a_passing_eval_is_run_once(marked_test: MarkedTest) -> None:
    calls: list[int] = []

    def evaluate() -> EvalRound:
        calls.append(1)
        return make_round(attempts=[make_attempt("c", assertions={"ok": True})])

    run(evaluate)
    assert len(calls) == 1, "nothing failed, so nothing was worth retrying"


def test_a_case_that_recovers_within_the_budget_passes(
    marked_test: MarkedTest,
) -> None:
    """The whole point. A flaky case that passes on a later round is absorbed,
    and does not fail the build."""
    run(_verdicts({"c": False}, {"c": True}))
    assert _recorded_attempts(marked_test, "c") == 2


def test_a_case_that_never_passes_fails(marked_test: MarkedTest) -> None:
    with pytest.raises(AssertionError, match="c"):
        run(_verdicts({"c": False}))


def test_the_budget_bounds_the_rounds(marked_test: MarkedTest) -> None:
    """`flake_reruns=3` is three retries, not three attempts and not unlimited."""
    with pytest.raises(AssertionError):
        run(_verdicts({"c": False}))
    assert _recorded_attempts(marked_test, "c") == 4


def test_rounds_stop_as_soon_as_every_case_has_passed(
    marked_test: MarkedTest,
) -> None:
    """A round costs a real run of the eval, so the budget is a ceiling and not
    a quota to spend."""
    run(_verdicts({"a": False, "b": True}, {"a": True, "b": True}))
    assert _recorded_attempts(marked_test, "a") == 2


def test_a_case_that_has_passed_is_not_un_passed_by_a_later_round(
    marked_test: MarkedTest,
) -> None:
    """A re-run of everything runs a settled case again. The latest verdict
    would let a rerun add flakiness where it must absorb it."""
    run(_verdicts({"a": True, "b": False}, {"a": False, "b": True}))

    case = marked_test.recorder.to_run_record().tests[TEST_NODEID].cases["a"]
    assert case.outcome == "passed", "the second round's bad luck took the pass away"


def test_every_round_is_recorded(marked_test: MarkedTest) -> None:
    """The run keeps what each round produced, and the pass-rate history pools
    those rounds."""
    run(_verdicts({"c": False}, {"c": False}, {"c": True}))
    assert _recorded_attempts(marked_test, "c") == 3


def test_a_round_reruns_the_whole_eval(
    marked_test: MarkedTest,
) -> None:
    rounds: list[int] = []

    def evaluate() -> EvalRound:
        rounds.append(1)
        return make_round(
            attempts=[
                make_attempt("a", assertions={"ok": True}),
                make_attempt("b", assertions={"ok": len(rounds) > 1}),
            ]
        )

    run(evaluate)
    assert _recorded_attempts(marked_test, "a") == 2, "the settled case ran again too"


def test_an_error_is_not_retried(marked_test: MarkedTest) -> None:
    """A crash is a bug or broken infrastructure, not flakiness, and a retry
    spends the budget on something it cannot fix."""
    calls: list[int] = []

    def evaluate() -> EvalRound:
        calls.append(1)
        return make_round(
            attempts=[make_attempt("c", errors=[AttemptErrorRecord(message="boom")])]
        )

    with pytest.raises(EvalExecutionError, match="task failures"):
        run(evaluate)
    assert len(calls) == 1


def test_without_a_budget_it_is_a_single_run(bind: Bind) -> None:
    active = bind()
    with pytest.raises(AssertionError):
        run(_verdicts({"c": False}))
    assert _recorded_attempts(active, "c") == 1


def test_the_gate_reads_the_settled_verdicts(marked_test: MarkedTest) -> None:
    """A case proven in an earlier round must not be re-judged on a later one."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run(_verdicts({"a": True, "b": False}, {"a": False, "b": True}))


# --- the eval and its arguments ---


def test_arguments_reach_the_eval(marked_test: MarkedTest) -> None:
    seen: list[tuple[str, str]] = []

    def evaluate(task: str, *, name: str) -> EvalRound:
        seen.append((task, name))
        return make_round(attempts=[make_attempt("c", assertions={"ok": True})])

    run(evaluate, "task", name="dataset")
    assert seen == [("task", "dataset")]


def test_a_further_round_is_given_the_arguments_again(marked_test: MarkedTest) -> None:
    """A round re-runs the eval, so one that lost the arguments would evaluate
    something other than what the first round did."""
    seen: list[tuple[str, str]] = []

    def evaluate(task: str, *, name: str) -> EvalRound:
        seen.append((task, name))
        return make_round(
            attempts=[make_attempt("c", assertions={"ok": len(seen) > 1})]
        )

    run(evaluate, "task", name="dataset")
    assert seen == [("task", "dataset"), ("task", "dataset")]


def test_an_async_eval_is_refused_and_its_body_never_runs(
    marked_test: MarkedTest,
) -> None:
    """`run` has no loop to await in. A call to the coroutine function only
    builds the coroutine. `run` refuses and closes that coroutine, so the call
    costs no round."""
    ran: list[int] = []

    async def evaluate() -> EvalRound:
        ran.append(1)
        return make_round()

    with pytest.raises(EvalDefinitionError, match="run_async"):
        run(evaluate)
    assert not ran, "the eval's body must not run without a loop to await it"


def test_an_eval_answering_with_a_coroutine_is_refused_and_leaves_no_warning(
    marked_test: MarkedTest,
) -> None:
    """A wrapper around a coroutine function does not read as one, so only what
    came back can carry this refusal. The coroutine is closed on the way out,
    because an un-awaited one warns when the collector reaches it, and warnings
    are errors here."""

    async def evaluate() -> EvalRound:
        return make_round()

    def wrapped() -> Any:
        return evaluate()

    refusal = ""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            run(wrapped)
        except EvalDefinitionError as exc:
            refusal = str(exc)
        # The traceback is gone with `exc`, so a coroutine nobody closed would
        # be collected here, inside the error filter.
        gc.collect()
    assert "run_async" in refusal, "the refusal points at the async entry point"


def test_run_async_given_a_coroutine_is_refused_and_leaves_no_warning(
    marked_test: MarkedTest,
) -> None:
    """`run_async(evaluate(task))` hands over one round's un-awaited result
    instead of the eval. The coroutine is closed on the way out, because an
    un-awaited one warns when the collector reaches it, and warnings are errors
    here."""

    async def evaluate() -> EvalRound:
        return make_round()

    already_started: Any = evaluate()
    refusal = ""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            asyncio.run(run_async(already_started))
        except EvalDefinitionError as exc:
            refusal = str(exc)
        gc.collect()
    assert "dataset.evaluate, task" in refusal, "the refusal shows the call that works"


def test_run_given_a_coroutine_is_refused_and_leaves_no_warning(
    marked_test: MarkedTest,
) -> None:
    """The same mistake against the sync entry point gets the same closing."""

    async def evaluate() -> EvalRound:
        return make_round()

    already_started: Any = evaluate()
    refusal = ""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            run(already_started)
        except EvalDefinitionError as exc:
            refusal = str(exc)
        gc.collect()
    assert "dataset.evaluate_sync, task" in refusal, (
        "the refusal shows the call that works"
    )


def test_run_async_gives_the_eval_its_arguments_every_round(
    marked_test: MarkedTest,
) -> None:
    seen: list[tuple[str, str]] = []

    async def evaluate(task: str, *, name: str) -> EvalRound:
        seen.append((task, name))
        return make_round(
            attempts=[make_attempt("c", assertions={"ok": len(seen) > 1})]
        )

    asyncio.run(run_async(evaluate, "task", name="dataset"))
    assert seen == [("task", "dataset"), ("task", "dataset")]


def test_run_async_drives_repeats_the_way_run_does(bind: Bind) -> None:
    """`run_async` carries its own copy of the repeats branch, so the two can
    drift. Every `repeats=` rule below is pinned for `run` above; this is the
    one that proves the async copy still has them at all."""
    active = bind(repeats=3)
    rounds: list[int] = []

    async def evaluate() -> EvalRound:
        rounds.append(1)
        return make_round(
            attempts=[make_attempt("c", assertions={"ok": True}, output=len(rounds))]
        )

    asyncio.run(run_async(evaluate))

    assert len(rounds) == 3, "each demanded attempt is its own round"
    assert _recorded_attempts(active, "c") == 3


def test_run_async_refuses_an_eval_that_does_not_re_run(bind: Bind) -> None:
    """The freshness guard is in the async copy too. Without it a repeat that
    hands back the same object spends every round on one set of verdicts."""
    bind(repeats=2)
    already_ran = make_round(attempts=[make_attempt("c", assertions={"ok": True})])

    async def evaluate() -> EvalRound:
        return already_ran

    with pytest.raises(EvalDefinitionError, match="did not re-run"):
        asyncio.run(run_async(evaluate))


def test_run_async_refuses_an_eval_there_is_nothing_to_await(
    marked_test: MarkedTest,
) -> None:
    """A sync eval handed here would be recorded with nothing ever awaited, so
    the refusal must name the wrong entry point instead of quiet success."""
    sync_eval: Any = make_round
    with pytest.raises(EvalDefinitionError, match="cannot be awaited"):
        asyncio.run(run_async(sync_eval))


# --- repeats=: demanded rounds ---


def test_repeats_drives_the_rounds_and_demands_all_pass(bind: Bind) -> None:
    active = bind(repeats=3)
    with pytest.raises(AssertionError, match="c"):
        run(_verdicts({"c": True}, {"c": True}, {"c": False}))
    assert _recorded_attempts(active, "c") == 3, (
        "every demanded round ran, the early passes settled nothing"
    )


def test_repeats_pass_when_every_round_passes(bind: Bind) -> None:
    active = bind(repeats=2)
    run(_verdicts({"c": True}, {"c": True}))
    assert _recorded_attempts(active, "c") == 2


def test_a_native_repeat_satisfies_repeats_in_one_round(bind: Bind) -> None:
    active = bind(repeats=2)
    calls: list[int] = []

    def evaluate() -> EvalRound:
        calls.append(1)
        return make_round(
            attempts=[
                make_attempt("c", assertions={"ok": True}),
                make_attempt("c", assertions={"ok": True}),
            ]
        )

    run(evaluate)
    assert len(calls) == 1, "the eval brought both attempts itself"
    assert _recorded_attempts(active, "c") == 2


def test_a_native_repeat_without_the_marker_is_refused(bind: Bind) -> None:
    bind()

    def evaluate() -> EvalRound:
        return make_round(
            attempts=[
                make_attempt("c", assertions={"ok": True}),
                make_attempt("c", assertions={"ok": True}),
            ]
        )

    with pytest.raises(EvalDefinitionError, match="Declare repeats=2"):
        run(evaluate)


def test_uneven_repetition_across_cases_is_refused(bind: Bind) -> None:
    bind(repeats=2)

    def evaluate() -> EvalRound:
        return make_round(
            attempts=[
                make_attempt("a", assertions={"ok": True}),
                make_attempt("a", assertions={"ok": True}),
                make_attempt("b", assertions={"ok": True}),
            ]
        )

    with pytest.raises(EvalDefinitionError, match="some cases and not others"):
        run(evaluate)


def test_a_driven_round_that_drops_a_case_is_refused(bind: Bind) -> None:
    """repeats demands N attempts for every case, so a round that misses one
    can never satisfy it, and a merge would gate a short count as met."""
    bind(repeats=2)
    with pytest.raises(EvalDefinitionError, match="missing the first round's"):
        run(
            _verdicts(
                {"a": True, "b": True},
                {"a": True},
            )
        )


def test_repeats_reads_the_markers_count(bind: Bind) -> None:
    """The count is written once, on the marker. An eval that wires a native
    repeat reads the count from here rather than states it again."""
    bind(repeats=3)
    assert repeats() == 3


def test_repeats_is_one_when_the_marker_demands_nothing(bind: Bind) -> None:
    """So the same eval body works with and without repeats= on the marker."""
    bind()
    assert repeats() == 1


def test_repeats_outside_a_marked_test_is_refused() -> None:
    with pytest.raises(EvalDefinitionError, match="outside a test marked"):
        repeats()
